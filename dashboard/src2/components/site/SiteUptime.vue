<template>
	<div
		v-if="!data || data[0].date === undefined"
		class="flex h-5/6 items-center justify-center"
	>
		<div class="text-base text-gray-700">No data</div>
	</div>
	<div v-else class="mx-4 mt-8" v-for="type in uptimeTypes" :key="type.key">
		<div class="flex h-10 justify-between">
			<div
				v-for="d in data"
				:key="d.date"
				class="w-1.5 rounded"
				:class="[
					d[type.key] === undefined
						? 'bg-white'
						: d[type.key] === 1
						? 'bg-green-500'
						: d[type.key] === 0
						? 'bg-red-500'
						: 'bg-yellow-500'
				]"
				:title="
					d[type.key]
						? `${formatDate(d.date)} | Uptime: ${(d.value * 100).toFixed(2)}%`
						: ''
				"
			></div>
		</div>
	</div>
</template>

<script>
import { DateTime } from 'luxon';
export default {
	name: 'SiteUptime',
	props: ['data', 'loading'],
	computed: {
		uptimeTypes() {
			return [{ key: 'value', label: 'Web' }];
		},
		subtitle() {
			if (!this.data) return '';

			let total = 0;
			let i = 0;
			for (; i < this.data.length; i++) {
				// there could be empty objects at the end of the array
				// so we don't have to count them
				if (typeof this.data[i].value !== 'number') break;

				total += this.data[i].value;
			}
			const average = ((total / i) * 100).toFixed(2);

			return !isNaN(average) ? `Average: ${average}%` : '';
		}
	},
	methods: {
		formatDate(date) {
			return DateTime.fromSQL(date).toLocaleString(DateTime.DATETIME_FULL);
		}
	}
};
</script>
