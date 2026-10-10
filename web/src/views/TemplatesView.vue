<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import StatePanel from "../components/StatePanel.vue";
import { hasPermission, readSession } from "../session";

// 有 templates.read 的角色可只读查看；新建等写操作只给 templates.manage（09 审查 M2）。
const canManage = computed(() => hasPermission(readSession(), "templates.manage"));
import { formatDateTime } from "../format";
import { ApiError } from "../api/client";
import { createTemplate, listTemplates, templateStatusLabel, type TemplateRecord } from "../api/templates";
import { useFieldErrors } from "../fieldErrors";

const page = ref(1);
const nameQuery = ref("");
const appliedName = ref("");
const items = ref<TemplateRecord[]>([]);
const total = ref(0);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const creating = ref(false);
const submitting = ref(false);
const name = ref("");
const category = ref("");
const description = ref("");
const formError = ref("");
const fields = useFieldErrors<"name" | "category">("tpl");
const pageCount = () => Math.max(1, Math.ceil(total.value / 20));

function messageOf(error: unknown) {
  return error instanceof ApiError ? error.message : "暂时不能连接服务。";
}

async function load() {
  phase.value = "loading";
  try {
    const result = await listTemplates(page.value, appliedName.value);
    items.value = result.items;
    total.value = result.total;
    phase.value = "ready";
  } catch (error) {
    phase.value = "error";
    errorText.value = messageOf(error);
  }
}

function search() {
  appliedName.value = nameQuery.value.trim();
  page.value = 1;
  void load();
}

function resetQuery() {
  nameQuery.value = "";
  search();
}

async function submitCreate() {
  formError.value = "";
  if (await fields.report({
    name: name.value.trim() ? "" : "请填写模板名称。",
    category: category.value.trim() ? "" : "请填写分类。",
  })) return;
  submitting.value = true;
  try {
    await createTemplate({
      name: name.value.trim(),
      category: category.value.trim(),
      description: description.value.trim(),
    });
    creating.value = false;
    name.value = "";
    category.value = "";
    description.value = "";
    page.value = 1;
    await load();
  } catch (error) {
    formError.value = messageOf(error);
  } finally {
    submitting.value = false;
  }
}

const createDialog = ref<HTMLDialogElement | null>(null);
const nameInput = ref<HTMLInputElement | null>(null);
watch(creating, async (open) => {
  await nextTick();
  const dialog = createDialog.value;
  if (!dialog) return;
  if (open && !dialog.open) { dialog.showModal(); nameInput.value?.focus(); }
  if (!open && dialog.open) dialog.close();
});
function dismissCreate() {
  if (!submitting.value) { creating.value = false; formError.value = ""; fields.clear(); }
}

onMounted(() => void load());
</script>

<template>
  <section class="view fill">
    <header class="page-head">
      <div><h1>模板管理</h1><p class="page-desc">按文档类别维护模板组；发布后的模板可在项目中新建文档时选用。</p></div>
      <div v-if="canManage" class="page-actions"><button type="button" class="primary" :disabled="creating" @click="creating = true"><svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>新建模板组</button></div>
    </header>
    <div class="card tpl-card">
      <form class="query" @submit.prevent="search">
        <div class="query-fields">
          <label class="search-box"><svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zM14 14l-3.5-3.5" /></svg><input v-model="nameQuery" class="control" type="search" placeholder="搜索模板名称" aria-label="模板名称" /></label>
        </div>
        <div class="query-actions">
          <button type="submit" class="primary">查询</button>
          <button type="button" class="ghost" @click="resetQuery">重置</button>
        </div>
      </form>
      <StatePanel v-if="phase === 'loading'" kind="loading" text="正在读取模板…" />
      <StatePanel v-else-if="phase === 'error'" kind="error" title="模板没有载入" :text="errorText"><button type="button" class="ghost" @click="load">重试</button></StatePanel>
      <StatePanel v-else-if="items.length === 0" :title="appliedName ? '没有符合条件的模板组' : '还没有模板组'" :text="appliedName ? '换个名称试试。' : '新建模板组后，可导入 Word 模板文件。'" />
      <div v-else class="table-wrap tpl-table">
        <table>
          <thead>
            <tr>
              <th>名称</th>
              <th class="col-cat">分类</th>
              <th class="col-num th-right">文档数</th>
              <th class="col-state">状态</th>
              <th class="col-time">最近更新</th>
              <th class="col-ops th-right"><span class="sr">操作</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in items" :key="item.id">
              <td><router-link class="link tpl-name" :to="{ name: 'template', params: { id: item.id } }">{{ item.name }}</router-link><span v-if="item.description" class="tpl-desc" :title="item.description">{{ item.description }}</span></td>
              <td>{{ item.category }}</td>
              <td class="num th-right">{{ item.documentCount }}</td>
              <td><span class="status" :data-status="templateStatusLabel[item.status]">{{ templateStatusLabel[item.status] }}</span></td>
              <td class="time num">{{ formatDateTime(item.updatedAt) }}</td>
              <td class="ops"><router-link class="link" :to="{ name: 'template', params: { id: item.id } }">详情</router-link></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="phase === 'ready' && items.length" class="pager">
        <span class="total">共 {{ total }} 条</span>
        <template v-if="total > 20">
          <button type="button" class="page" :disabled="page <= 1" @click="page -= 1; load()">上一页</button>
          <span>第 {{ page }} / {{ pageCount() }} 页</span>
          <button type="button" class="page" :disabled="page >= pageCount()" @click="page += 1; load()">下一页</button>
        </template>
      </div>
    </div>

    <dialog ref="createDialog" class="app-dialog" aria-labelledby="create-tpl-title" @cancel.prevent="dismissCreate">
      <form v-if="creating" class="create" novalidate @submit.prevent="submitCreate">
        <div class="dialog-head"><h2 id="create-tpl-title">新建模板组</h2><button type="button" class="subtle icon-close" aria-label="关闭" :disabled="submitting" @click="dismissCreate"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button></div>
        <div class="dialog-fields">
          <div class="field"><label for="tpl-name" class="required">模板名称</label><input id="tpl-name" ref="nameInput" v-model="name" class="control" v-bind="fields.invalid('name')" type="text" :disabled="submitting" @input="fields.clear('name')" /><span v-if="fields.errors.value.name" :id="fields.errorId('name')" class="field-error" role="alert">{{ fields.errors.value.name }}</span></div>
          <div class="field"><label for="tpl-category" class="required">分类</label><input id="tpl-category" v-model="category" class="control" v-bind="fields.invalid('category')" type="text" placeholder="如：软件需求规格说明" :disabled="submitting" @input="fields.clear('category')" /><span v-if="fields.errors.value.category" :id="fields.errorId('category')" class="field-error" role="alert">{{ fields.errors.value.category }}</span></div>
          <div class="field span-2"><label for="tpl-desc">描述</label><textarea id="tpl-desc" v-model="description" class="control" rows="3" :disabled="submitting"></textarea></div>
        </div>
        <p v-if="formError" class="form-alert dialog-status" role="alert">{{ formError }}</p>
        <div class="dialog-actions">
          <button type="button" class="ghost" :disabled="submitting" @click="dismissCreate">取消</button>
          <button type="submit" class="primary" :disabled="submitting">{{ submitting ? "正在创建" : "创建" }}</button>
        </div>
      </form>
    </dialog>
  </section>
</template>

<style scoped>
.tpl-card { overflow: hidden; }
.tpl-table { flex: 1; min-height: 0; overflow-y: auto; }
.tpl-table table { table-layout: fixed; }
.col-cat { width: 18%; }
.col-num { width: 8%; }
.col-state { width: 10%; }
.col-time { width: 12%; }
.col-ops { width: 8%; }
.tpl-name { display: block; font-weight: var(--weight-medium); }
.tpl-desc { display: block; overflow: hidden; margin-top: 2px; color: var(--text-tertiary); font-size: var(--text-xs); text-overflow: ellipsis; white-space: nowrap; }
.create { display: block; padding: 0; }
</style>
