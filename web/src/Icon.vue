<script setup>
defineProps({ name: String, size: { type: Number, default: 20 } });
const paths = {
  panel:
    "M9 3v18M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z",
  workspace:
    "M12 3v4m0 10v4M3 12h4m10 0h4M7 7l3 3m4 4 3 3m0-10-3 3m-4 4-3 3M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z",
  file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6M8 13h8m-8 4h5",
  history: "M3 11a9 9 0 1 1 2.6 7.4M3 4v7h7m2-4v5l3 2",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM9 3l1-1h4l1 3 3 1 3 1v4l-2 2-1 3-1 3h-4l-2-2-3-1-3-1v-4l2-2 1-3Z",
  sliders: "M4 6h4m4 0h8M4 12h10m4 0h2M4 18h2m4 0h10M8 3v6m6 0v6M6 15v6",
  log: "M4 4h16v16H4ZM8 8l3 3-3 3m5 0h3",
  plus: "M12 5v14M5 12h14",
  arrow: "M12 19V5m-6 6 6-6 6 6",
  close: "m6 6 12 12M6 18 18 6",
  cloud: "M7 18a5 5 0 1 1 .5-10A6 6 0 0 1 19 9a4.5 4.5 0 0 1-1.5 9Z",
  check: "m5 12 4 4L19 6",
  network:
    "M3 8a14 14 0 0 1 18 0M6 12a9 9 0 0 1 12 0m-9 4a4 4 0 0 1 6 0m-3 4h.01",
  chevron: "m8 10 4 4 4-4",
};
</script>
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] || paths.file" />
  </svg>
</template>
