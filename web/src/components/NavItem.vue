<script setup lang="ts">
import type { RouteLocationRaw } from "vue-router";

defineProps<{
  to: RouteLocationRaw;
  current: boolean;
  label?: string;
}>();
</script>

<template>
  <router-link v-slot="{ href, navigate }" :to="to" custom>
    <a :href="href" :class="{ current }" :aria-current="current ? 'page' : undefined" :title="label" @click="navigate">
      <slot />
    </a>
  </router-link>
</template>
