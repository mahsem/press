# Copyright (c) 2020, Frappe and contributors
# For license information, please see license.txt
from __future__ import annotations

import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Optional

import frappe
from frappe.model.document import Document
from frappe.query_builder import Interval
from frappe.query_builder.functions import Now

from press.utils import log_error

if TYPE_CHECKING:
	from press.press.doctype.app_source.app_source import AppSource


class GitHubWebhookLog(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		branch: DF.Data | None
		event: DF.Data
		git_reference_type: DF.Literal["tag", "branch"]
		github_installation_id: DF.Data | None
		payload: DF.Code
		repository: DF.Data | None
		repository_owner: DF.Data | None
		signature: DF.Data
		tag: DF.Data | None
	# end: auto-generated types

	def validate(self):  # noqa: C901
		secret = frappe.db.get_single_value("Press Settings", "github_webhook_secret")
		digest = hmac.HMAC(secret.encode(), self.payload.encode(), hashlib.sha1)
		if not hmac.compare_digest(digest.hexdigest(), self.signature):
			frappe.throw("Invalid Signature")

		payload = self.get_parsed_payload()
		self.github_installation_id = payload.get("installation", {}).get("id")

		repository_detail = get_repository_details_from_payload(payload)
		self.repository = repository_detail["name"]
		self.repository_owner = repository_detail["owner"]

		if self.event == "push":
			ref_types = {"tags": "tag", "heads": "branch"}
			self.git_reference_type = ref_types[payload.ref.split("/")[1]]
			ref = payload.ref.split("/", 2)[2]
			if self.git_reference_type == "tag":
				self.tag = ref
			elif self.git_reference_type == "branch":
				self.branch = ref
		elif self.event == "create":
			self.git_reference_type = payload.ref_type
			if self.git_reference_type == "tag":
				self.tag = payload.ref
			elif self.git_reference_type == "branch":
				self.branch = payload.ref
		elif self.event == "release":
			self.tag = payload.release.get("tag_name")

		self.payload = json.dumps(payload, indent=4, sort_keys=True)

	def handle_events(self):
		if self.event == "push":
			self.handle_push_event()
		elif self.event == "installation":
			self.handle_installation_event()
		elif self.event == "installation_repositories":
			self.handle_repository_installation_event()
		frappe.db.commit()

	def handle_push_event(self):
		payload = self.get_parsed_payload()
		if self.git_reference_type == "branch":
			self.create_app_releases(payload)
		elif self.git_reference_type == "tag":
			self.create_app_tag(payload)

	def handle_installation_event(self):
		payload = self.get_parsed_payload()
		action = payload.get("action")
		if action == "created" or action == "unsuspend":
			self.handle_installation_created(payload)
		elif action == "deleted" or action == "suspend":
			self.handle_installation_deletion(payload)

	def handle_repository_installation_event(self):
		payload = self.get_parsed_payload()
		if payload["action"] not in ["added", "removed"]:
			return
		owner = payload["installation"]["account"]["login"]
		self.update_installation_ids(owner)

		for repo in payload.get("repositories_removed", []):
			set_uninstalled(owner, repo["name"])

	def handle_installation_created(self, payload):
		owner = payload["installation"]["account"]["login"]
		self.update_installation_ids(owner)

	def handle_installation_deletion(self, payload):
		owner = payload["installation"]["account"]["login"]
		repositories = payload.get("repositories", [])

		for repo in repositories:
			set_uninstalled(owner, repo["name"])

		if len(repositories) == 0:
			# Set all sources as uninstalled
			set_uninstalled(owner)

	def update_installation_ids(self, owner: str):
		for name in get_sources(owner):
			doc: "AppSource" = frappe.get_doc("App Source", name)
			if not self.should_update_app_source(doc):
				continue

			self.update_app_source_installation_id(doc)

	def update_app_source_installation_id(self, doc: "AppSource"):
		doc.github_installation_id = self.github_installation_id
		"""
		These two are assumptions, they will be resolved when
		`doc.create_release` is called.

		It is not called here, because it requires polling GitHub
		which if the repository owner has several apps gets us
		rate limited.
		"""
		doc.uninstalled = False
		doc.last_github_poll_failed = False
		doc.db_update()

	def should_update_app_source(self, doc: "AppSource"):
		if doc.uninstalled or doc.last_github_poll_failed:
			return True

		return doc.github_installation_id != self.github_installation_id

	def get_parsed_payload(self):
		return frappe.parse_json(self.payload)

	def create_app_releases(self, payload):
		sources = frappe.db.get_all(
			"App Source",
			filters={
				"branch": self.branch,
				"repository": self.repository,
				"repository_owner": self.repository_owner,
				"enabled": 1,
			},
			fields=["name", "app"],
		)

		commit = payload.get("head_commit", {})
		if len(sources) == 0 or not commit or not commit.get("id"):
			return

		for source in sources:
			try:
				create_app_release(source.name, source.app, commit)
			except Exception:
				log_error("App Release Creation Error", payload=payload, doc=self)

	def create_app_tag(self, payload):
		commit = payload.get("head_commit", {})
		if not commit or not commit.get("id"):
			return

		tag = frappe.get_doc(
			{
				"doctype": "App Tag",
				"tag": self.tag,
				"hash": commit.get("id"),
				"timestamp": commit.get("timestamp"),
				"repository": self.repository,
				"repository_owner": self.repository_owner,
				"github_installation_id": self.github_installation_id,
			}
		)

		try:
			tag.insert()
		except Exception:
			log_error("App Tag Creation Error", payload=payload, doc=self)

	@staticmethod
	def clear_old_logs(days=30):
		table = frappe.qb.DocType("GitHub Webhook Log")
		frappe.db.delete(table, filters=(table.creation < (Now() - Interval(days=days))))


def set_uninstalled(owner: str, repository: str | None = None):
	for name in get_sources(owner, repository):
		frappe.db.set_value("App Source", name, "uninstalled", True)


def get_sources(owner: str, repository: str | None = None) -> "list[str]":
	filters = {"repository_owner": owner}
	if repository:
		filters["repository"] = repository

	return frappe.db.get_all(
		"App Source",
		filters=filters,
		pluck="name",
	)


def get_repository_details_from_payload(payload: dict):
	r = payload.get("repository", {})
	repo = r.get("name")
	owner = r.get("owner", {}).get("login")

	repos = payload.get("repositories_added", [])
	if not repo and len(repos) == 1:
		repo = repos[0].get("name")

	if not owner and repos:
		owner = repos[0].get("full_name", "").split("/")[0] or None

	if not owner:
		owner = payload.get("installation", {}).get("account", {}).get("login")

	return dict(name=repo, owner=owner)


def create_app_release(source: str, app: str, commit: dict):
	release = frappe.get_doc(
		{
			"doctype": "App Release",
			"app": app,
			"source": source,
			"hash": commit.get("id"),
			"message": commit.get("message", "MESSAGE NOT FOUND"),
			"author": commit.get("author", {}).get("name", "AUTHOR NOT FOUND"),
		}
	)
	release.insert(ignore_permissions=True)
