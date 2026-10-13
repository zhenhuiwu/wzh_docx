<script setup lang="ts">
import StatePanel from "../components/StatePanel.vue";
</script>

<template>
  <section class="view fill">
    <div class="card">
      <StatePanel title="页面不存在或已移动" text="请检查地址，或从左侧导航进入需要的页面。">
        <router-link class="primary" :to="{ name: 'projects' }">返回项目列表</router-link>
      </StatePanel>
    </div>
  </section>
</template>
