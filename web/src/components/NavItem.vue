<script setup lang="ts">
import type { RouteLocationRaw } from "vue-router";

defineProps<{
  to: RouteLocationRaw;
  current: boolean;
}>();
</script>

<template>
  <router-link v-slot="{ href, navigate }" :to="to" custom>
    <a :href="href" :class="{ current }" :aria-current="current ? 'page' : undefined" @click="navigate">
      <slot />
    </a>
  </router-link>
</template>
