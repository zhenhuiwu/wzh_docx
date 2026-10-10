<script setup lang="ts">
/** 冲突面板（02 §3.4；01 §4.4）：按“处”展示，处理动作统一走 resolve 合一接口。 */
import { computed, onMounted, ref, watch } from "vue";
import { ApiError } from "../api/client";
import { getConflictCopy, listConflicts, resolveConflict, type ConflictItem, type ResolveRequestItem, type ResolveResult } from "../api/documents";
import { ACTION_DONE_TEXT, REASON_TEXT, USE_THEIRS_CONFIRM, ResolveKeys, actionsFor, classifyResolveError, type ActionChoice } from "../conflictModel";
import { locationText, shortTime } from "../saveModel";

const props = defineProps<{
  documentId: string; documentName: string; revision: number; canViewMembers: boolean; canResolve: boolean;
  /** 处理前由工作台先保存正文；返回 false 表示仍有未保存修改，不能处理。 */
  beforeResolve: () => Promise<boolean>;
}>();
const emit = defineEmits<{ close: []; resolved: [result: ResolveResult]; count: [n: number] }>();

const tab = ref<"mine" | "members">("mine");
const items = ref<ConflictItem[]>([]);
const handled = ref<ConflictItem[]>([]);
const phase = ref<"loading" | "ready" | "error">("loading");
const loadError = ref("");
const busyKey = ref("");
const cardError = ref<Record<string, string>>({});
const cardNotice = ref<Record<string, string>>({});
const manualOpen = ref<Record<string, string>>({});
const confirmTheirs = ref<ConflictItem | null>(null);
const handledOpen = ref(false);
const keys = new ResolveKeys();
const id = (item: ConflictItem) => `${item.conflictId}:${item.itemIndex}`;

async function load() {
  phase.value = "loading";
  try {
    const actor = tab.value === "members" ? "all" : "me";
    const [open, done] = await Promise.all([listConflicts(props.documentId, "open", actor), listConflicts(props.documentId, "resolved", actor)]);
    items.value = open;
    handled.value = done;
    phase.value = "ready";
    if (tab.value === "mine") emit("count", open.length);
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : "冲突列表加载失败";
    phase.value = "error";
  }
}
onMounted(load);
watch(tab, load);
defineExpose({ load });

const empty = computed(() => phase.value === "ready" && !items.value.length);
function whenOf(item: ConflictItem) {
  const at = item.theirs?.deleted ? item.theirs?.deletedAt : item.theirs?.updatedAt;
  return at ? shortTime(at) : "";
}
function whoOf(item: ConflictItem) {
  return (item.theirs?.deleted ? item.theirs?.deletedBy?.name : item.theirs?.updatedBy?.name) || "其他成员";
}

async function apply(item: ConflictItem, choice: ActionChoice, markdown?: string) {
  if (!props.canResolve || busyKey.value) return;
  if (choice.action === "use_theirs" && confirmTheirs.value !== item) { confirmTheirs.value = item; return; }
  confirmTheirs.value = null;
  const request: ResolveRequestItem = { itemIndex: item.itemIndex, action: choice.action };
  if (choice.action === "keep_mine" || choice.action === "manual" || choice.action === "keep_both") request.theirsRevision = item.theirs?.revision ?? null;
  if (choice.action === "manual") request.markdown = markdown ?? "";
  if (choice.placement) request.placement = { mode: choice.placement };
  const key = keys.keyFor(JSON.stringify([item.conflictId, request]));
  busyKey.value = id(item);
  cardError.value = { ...cardError.value, [id(item)]: "" };
  try {
    if (!(await props.beforeResolve())) {
      cardError.value = { ...cardError.value, [id(item)]: "正文还有修改没保存成功，请先保存后再处理冲突。" };
      return;
    }
    const result = await resolveConflict(props.documentId, item.conflictId, key, props.revision, [request]);
    keys.done();
    const fallback = result.items.some(entry => entry.placementFallback);
    emit("resolved", result);
    if (fallback) cardNotice.value = { ...cardNotice.value, [id(item)]: "原章节已不存在，已放在最近的位置。" };
    delete manualOpen.value[id(item)];
    await load();
  } catch (error) {
    const apiError = error instanceof ApiError ? error : new ApiError(0, "DEPENDENCY_UNAVAILABLE", "暂时不能连接服务。");
    switch (classifyResolveError(apiError)) {
      case "archive":
        keys.done();
        await load();
        return;
      case "reconflict": {
        keys.done();
        const fresh = (apiError.details?.conflicts as ConflictItem[] | undefined)?.find(entry => entry.itemIndex === item.itemIndex);
        if (fresh) {
          items.value = items.value.map(entry => entry === item ? { ...entry, ...fresh, conflictId: item.conflictId } : entry);
          cardNotice.value = { ...cardNotice.value, [id(item)]: `${fresh.theirs?.updatedBy?.name || "对方"} 又修改了一次，请重新确认。` };
        }
        return;
      }
      case "retry":
        cardError.value = { ...cardError.value, [id(item)]: "网络不稳定，处理没有完成。点击同一按钮会安全重试。" };
        return;
      default:
        keys.done();
        cardError.value = { ...cardError.value, [id(item)]: apiError.message };
    }
  } finally {
    busyKey.value = "";
  }
}

function openManual(item: ConflictItem) {
  manualOpen.value = { ...manualOpen.value, [id(item)]: item.theirs?.markdown || item.mine?.markdown || "" };
}
function insertMine(item: ConflictItem) {
  manualOpen.value = { ...manualOpen.value, [id(item)]: `${manualOpen.value[id(item)] || ""}\n\n${item.mine?.markdown || ""}`.trim() };
}
async function downloadCopy(item: ConflictItem) {
  try {
    const copy = await getConflictCopy(props.documentId, item.conflictId);
    const url = URL.createObjectURL(new Blob([copy.mineMarkdown], { type: "text/markdown;charset=utf-8" }));
    const link = window.document.createElement("a");
    link.href = url;
    link.download = `${props.documentName}-冲突副本-${shortTime(copy.createdAt).replace(/[: ]/g, "")}.md`;
    window.document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    cardError.value = { ...cardError.value, [id(item)]: error instanceof Error ? error.message : "下载失败" };
  }
}
</script>

<template>
  <aside class="conflict-panel" aria-label="冲突处理">
    <header class="conflict-head">
      <h2>冲突 · {{ items.length }} 处待处理</h2>
      <button type="button" class="subtle icon-close" aria-label="收起冲突面板" @click="emit('close')"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button>
    </header>
    <p class="conflict-safe" role="note"><svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3 3 7-7" /></svg>冲突内容已单独保存在服务器，不会丢失。其他段落的修改已正常保存。</p>
    <nav v-if="canViewMembers" class="conflict-tabs" role="tablist" aria-label="冲突范围">
      <button type="button" role="tab" class="subtle" :aria-selected="tab === 'mine'" :class="{ on: tab === 'mine' }" @click="tab = 'mine'">我的冲突</button>
      <button type="button" role="tab" class="subtle" :aria-selected="tab === 'members'" :class="{ on: tab === 'members' }" @click="tab = 'members'">成员冲突</button>
    </nav>

    <div class="conflict-body">
      <template v-if="phase === 'loading'"><div class="ccard skeleton" aria-hidden="true" /><div class="ccard skeleton" aria-hidden="true" /><p class="sr-only" role="status">正在加载冲突…</p></template>
      <div v-else-if="phase === 'error'" class="conflict-empty"><p role="alert">冲突列表加载失败：{{ loadError }}</p><button type="button" class="ghost" @click="load">重试</button></div>
      <p v-else-if="empty" class="conflict-empty" role="status">没有待处理的冲突</p>
      <article v-for="item in items" v-else :key="id(item)" class="ccard" :data-conflict="id(item)" :aria-busy="busyKey === id(item)">
        <p v-if="cardNotice[id(item)]" class="ccard-notice" role="status">{{ cardNotice[id(item)] }}</p>
        <header class="ccard-head"><strong>{{ REASON_TEXT[item.reason] }}</strong><span class="muted">{{ whenOf(item) }}</span></header>
        <p class="ccard-loc">{{ locationText(item.location) }}</p>
        <p v-if="tab === 'members'" class="muted">提交人：{{ item.actor?.name || "未记录" }}（只读，仅本人可以处理）</p>
        <p v-if="item.reason === 'DELETED' && item.theirs?.deleted" class="ccard-desc">{{ whoOf(item) }} 于 {{ whenOf(item) }} 删除了这一段，但你修改过它。</p>
        <p v-else-if="item.reason === 'DELETED'" class="ccard-desc">你删除了这一段，但 {{ whoOf(item) }} 修改过它。</p>
        <p v-else-if="item.reason === 'FORMAT'" class="ccard-desc">合并后的内容无法写回原 Word，正文保持对方的版本。你可以下载自己的副本后手动处理。</p>
        <section class="ccard-side theirs">
          <h3>{{ item.theirs?.deleted ? "删除前的内容" : `${whoOf(item)} 的版本 · 已在正文中` }}</h3>
          <pre :class="{ gone: item.theirs?.deleted }">{{ item.theirs?.markdown || "（无）" }}</pre>
        </section>
        <section class="ccard-side mine">
          <h3>我的版本 · 未写入正文</h3>
          <pre>{{ item.mine?.deleted ? "（我删除了这一段）" : item.mine?.markdown }}</pre>
        </section>
        <details class="ccard-base"><summary>查看修改前的原内容</summary><pre>{{ item.base?.markdown || "（无）" }}</pre></details>

        <div v-if="manualOpen[id(item)] !== undefined" class="ccard-manual">
          <label class="stacked-label" :for="`manual-${id(item)}`">合并后的内容</label>
          <textarea :id="`manual-${id(item)}`" v-model="manualOpen[id(item)]" class="control" rows="6" :aria-invalid="!manualOpen[id(item)]?.trim()" :aria-describedby="`manual-hint-${id(item)}`" />
          <span v-if="!manualOpen[id(item)]?.trim()" :id="`manual-hint-${id(item)}`" class="field-error" role="alert">内容不能为空；如不需要这段内容，请使用对方的版本。</span>
          <div class="ccard-actions">
            <button type="button" class="ghost" @click="insertMine(item)">插入我的版本</button>
            <button type="button" class="ghost" @click="delete manualOpen[id(item)]">取消</button>
            <button type="button" class="primary" :disabled="!manualOpen[id(item)]?.trim() || Boolean(busyKey)" @click="apply(item, { action: 'manual', label: '手动合并' }, manualOpen[id(item)])">{{ busyKey === id(item) ? "正在处理…" : "采用合并结果" }}</button>
          </div>
        </div>
        <div v-else-if="confirmTheirs === item" class="ccard-confirm" role="alertdialog" aria-label="使用对方的版本">
          <p>{{ USE_THEIRS_CONFIRM }}</p>
          <div class="ccard-actions"><button type="button" class="ghost" @click="confirmTheirs = null">返回</button><button type="button" class="danger" @click="apply(item, { action: 'use_theirs', label: '使用对方的版本' })">使用对方的版本</button></div>
        </div>
        <div v-else-if="tab === 'mine' && canResolve" class="ccard-actions">
          <template v-for="choice in actionsFor(item)" :key="choice.label">
            <button v-if="choice.action === 'manual'" type="button" class="ghost" :disabled="Boolean(busyKey)" @click="openManual(item)">{{ choice.label }}</button>
            <button v-else type="button" :class="choice.action === 'keep_mine' || choice.action === 'restore_as_new' && choice.placement === 'after_nearest_live' ? 'primary' : 'ghost'" :disabled="Boolean(busyKey)" @click="apply(item, choice)">{{ busyKey === id(item) && choice.action !== 'use_theirs' ? "正在处理…" : choice.label }}</button>
          </template>
          <button type="button" class="link" @click="downloadCopy(item)">下载我的副本</button>
        </div>
        <p v-if="cardError[id(item)]" class="field-error" role="alert">{{ cardError[id(item)] }}</p>
      </article>

      <details v-if="handled.length" class="conflict-handled" :open="handledOpen" @toggle="handledOpen = ($event.target as HTMLDetailsElement).open">
        <summary>已处理（{{ handled.length }}）</summary>
        <ul>
          <li v-for="item in handled" :key="id(item)">
            <span>{{ locationText(item.location) }} · {{ ACTION_DONE_TEXT[item.action || ''] || item.action }}<template v-if="item.resolvedAt"> · {{ shortTime(item.resolvedAt) }}</template></span>
            <button type="button" class="link" @click="downloadCopy(item)">下载我的副本</button>
          </li>
        </ul>
      </details>
    </div>
  </aside>
</template>

<style scoped>
.conflict-panel { display:flex; flex-direction:column; gap:var(--space-3); min-height:0; min-width:0; padding:var(--space-4); background:var(--bg-surface, var(--sheet)); border-left:1px solid var(--border-default, var(--rule)); font-size:var(--text-sm); }
.conflict-head { display:flex; align-items:center; justify-content:space-between; gap:var(--space-2); }
.conflict-head h2 { margin:0; font-size:var(--text-md); font-weight:var(--weight-semibold, 600); }
.conflict-safe { display:flex; gap:var(--space-2); align-items:flex-start; margin:0; padding:var(--space-2) var(--space-3); border-radius:var(--radius-control); background:var(--success-50); color:var(--success-600); }
.conflict-safe .ico { flex:none; width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:2; }
.conflict-tabs { display:flex; gap:var(--space-1); }
.conflict-tabs .on { background:var(--bg-active); color:var(--brand-600); }
.conflict-body { flex:1; min-height:0; overflow:auto; display:flex; flex-direction:column; gap:var(--space-3); }
.conflict-empty { color:var(--text-tertiary, var(--muted)); text-align:center; padding:var(--space-5) 0; }
.ccard { display:grid; gap:var(--space-2); padding:var(--space-3); border:1px solid var(--danger-200); border-radius:var(--radius-sheet, var(--radius-control)); background:var(--bg-surface, var(--sheet)); }
.ccard.skeleton { min-height:calc(var(--space-7) * 3); border-color:var(--border-default, var(--rule)); background:var(--bg-subtle); }
.ccard-head { display:flex; justify-content:space-between; gap:var(--space-2); color:var(--danger-600); }
.ccard-loc { margin:0; color:var(--text-secondary, var(--muted)); overflow-wrap:anywhere; }
.ccard-desc { margin:0; }
.ccard-notice { margin:0; padding:var(--space-1) var(--space-2); border-radius:var(--radius-control); background:var(--warning-50); color:var(--warning-600); }
.ccard-side h3 { margin:0 0 var(--space-1); font-size:var(--text-xs); font-weight:var(--weight-medium, 500); color:var(--text-tertiary, var(--muted)); }
.ccard-side pre, .ccard-base pre { margin:0; padding:var(--space-2); white-space:pre-wrap; overflow-wrap:anywhere; font-family:inherit; border-radius:var(--radius-control); background:var(--bg-subtle); max-height:calc(var(--space-7) * 5); overflow:auto; }
.ccard-side.theirs pre { background:var(--diff-ins-bg, var(--bg-subtle)); }
.ccard-side.mine pre { background:var(--warning-50); }
.ccard-side pre.gone { text-decoration:line-through; color:var(--text-tertiary, var(--muted)); background:var(--bg-subtle); }
.ccard-base summary { cursor:pointer; color:var(--text-link, var(--brand-600)); }
.ccard-actions { display:flex; flex-wrap:wrap; gap:var(--space-2); align-items:center; }
.ccard-manual { display:grid; gap:var(--space-2); }
.ccard-manual textarea { width:100%; resize:vertical; }
.ccard-confirm p { margin:0 0 var(--space-2); }
.conflict-handled summary { cursor:pointer; color:var(--text-secondary, var(--muted)); }
.conflict-handled ul { list-style:none; margin:var(--space-2) 0 0; padding:0; display:grid; gap:var(--space-2); }
.conflict-handled li { display:flex; justify-content:space-between; gap:var(--space-2); overflow-wrap:anywhere; }
.sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); }
</style>
