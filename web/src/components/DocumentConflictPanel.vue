<script setup lang="ts">
/** 冲突面板（02 §3.4；01 §4.4）：按“处”展示，处理动作统一走 resolve 合一接口。 */
import { computed, onMounted, ref, watch } from "vue";
import ConflictCompareDialog from "./ConflictCompareDialog.vue";
import { diffWords, isTable, tableCellDiffs } from "../textDiff";
import { ApiError } from "../api/client";
import { getConflictCopy, listConflicts, resolveConflict, type ConflictItem, type ResolveRequestItem, type ResolveResult } from "../api/documents";
import { ACTION_DONE_TEXT, REASON_TEXT, USE_THEIRS_CONFIRM, ResolveKeys, actionsFor, classifyResolveError, type ActionChoice } from "../conflictModel";
import { locationText, shortTime } from "../saveModel";
import { continuedChars, type LiveMine } from "../frozenConflict";

const props = defineProps<{
  documentId: string; documentName: string; revision: number; canViewMembers: boolean; canResolve: boolean;
  /** 打开时的页签与页签上的数字（界面验收 M2/M3）。 */
  initialTab?: "mine" | "members"; mineCount?: number; memberCount?: number;
  /** 正文里只能按章节/序号推测位置的冲突（审查员第 4 条：位置行后加“（大致）”）。 */
  approxKeys?: string[];
  /** 处理前由工作台先保存正文；返回 false 表示仍有未保存修改，不能处理。 */
  beforeResolve: () => Promise<boolean>;
  /** 方案 C：冻结区里正在写的“我的版本”。 */
  liveMine?: Record<string, LiveMine>;
}>();
const emit = defineEmits<{ close: []; resolved: [result: ResolveResult]; count: [n: number]; locate: [item: ConflictItem] }>();

const tab = ref<"mine" | "members">(props.initialTab || "mine");
const items = ref<ConflictItem[]>([]);
const handled = ref<ConflictItem[]>([]);
const phase = ref<"loading" | "ready" | "error">("loading");
const loadError = ref("");
const busyKey = ref("");
/** 只在被点击的按钮上显示“正在处理…”（审查 S6）。 */
const busyLabel = ref("");
const comparing = ref<ConflictItem | null>(null);
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
    // 没有待处理时直接展开“已处理”，处理完的记录仍可查看和下载（界面验收 M2）
    if (!open.length && done.length) handledOpen.value = true;
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

// —— 方案 C：我的版本以最新快照为准（编辑区里正在写的优先）——
const live = (item: ConflictItem) => tab.value === "mine" ? props.liveMine?.[id(item)] : undefined;
const frozenHere = (item: ConflictItem) => Boolean(live(item));
function mineText(item: ConflictItem): string {
  if (item.mine?.deleted) return "";
  return live(item)?.markdown ?? item.mineLatest?.markdown ?? item.mine?.markdown ?? "";
}
/** 我在编辑区把这一段的文字删光了（不是删除段落）：只能用对方的或手动合并。 */
const mineEmptied = (item: ConflictItem) => !item.mine?.deleted && Boolean(item.mine?.markdown?.trim()) && !mineText(item).trim();
const continued = (item: ConflictItem) => item.mine?.deleted ? 0 : continuedChars(item.mine?.markdown || "", mineText(item));
function hm(at: string | null | undefined) {
  if (!at) return "";
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" });
}
/** “含冲突后续写的 N 字 · 已存为冲突副本 HH:mm / 仅在本机 · 联网后自动保存” */
function mineStatus(item: ConflictItem): string {
  const n = continued(item);
  const state = live(item);
  if (state && !state.saved) return `${n ? `含冲突后续写的 ${n} 字 · ` : ""}仅在本机 · 联网后自动保存`;
  if (!n) return "";
  const at = state?.savedAt || item.mineLatest?.savedAt;
  return `含冲突后续写的 ${n} 字${at ? ` · 已存为冲突副本 ${hm(at)}` : ""}`;
}
const withLatest = (item: ConflictItem): ConflictItem => tab.value === "mine" && !item.mine?.deleted ? { ...item, mine: { ...item.mine, markdown: mineText(item) } } : item;
const theirsConfirm = (item: ConflictItem) => frozenHere(item)
  ? `编辑区里这一段会换成 ${whoOf(item)} 的版本。你的版本（含后来续写的内容）仍保存在冲突记录中，可在「已处理」里查看和下载。`
  : USE_THEIRS_CONFIRM;

async function apply(item: ConflictItem, choice: ActionChoice, markdown?: string) {
  if (!props.canResolve || busyKey.value) return;
  if (choice.action === "use_theirs" && confirmTheirs.value !== item) { confirmTheirs.value = item; return; }
  confirmTheirs.value = null;
  const request: ResolveRequestItem = { itemIndex: item.itemIndex, action: choice.action };
  if (choice.action === "keep_mine" || choice.action === "manual" || choice.action === "keep_both") request.theirsRevision = item.theirs?.revision ?? null;
  if (choice.action === "manual") request.markdown = markdown ?? "";
  if (choice.placement) request.placement = { mode: choice.placement };
  busyKey.value = id(item);
  busyLabel.value = choice.label;
  cardError.value = { ...cardError.value, [id(item)]: "" };
  try {
    if (!(await props.beforeResolve())) {
      cardError.value = { ...cardError.value, [id(item)]: "正文还有修改没保存成功，请先保存后再处理冲突。" };
      return;
    }
    // 审查点 3：带上我看到的“我的版本”快照（处理前已把编辑区里的续写存上）；服务器上不是这一份时 409，不用旧快照处理
    if (tab.value === "mine") request.mineSnapshotId = (live(item) ? live(item)!.snapshotId : item.mineLatest?.snapshotId) ?? "";
    const key = keys.keyFor(JSON.stringify([item.conflictId, request]));
    const result = await resolveConflict(props.documentId, item.conflictId, key, props.revision, [request]);
    keys.done();
    const fallback = result.items.some(entry => entry.placementFallback);
    emit("resolved", { ...result, conflictId: result.conflictId ?? item.conflictId, action: result.action ?? choice.action, theirsName: result.theirsName ?? whoOf(item) });
    if (fallback) cardNotice.value = { ...cardNotice.value, [id(item)]: "位置已变化，请检查恢复的段落。" };
    delete manualOpen.value[id(item)];
    await load();
  } catch (error) {
    const apiError = error instanceof ApiError ? error : new ApiError(0, "DEPENDENCY_UNAVAILABLE", "暂时不能连接服务。");
    if (apiError.code === "MINE_SNAPSHOT_CHANGED") {
      keys.done();
      await load();
      cardNotice.value = { ...cardNotice.value, [id(item)]: "你的版本刚有更新（可能来自另一个标签页），请确认最新内容后再处理。" };
      return;
    }
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
    busyLabel.value = "";
  }
}

const diffOf = (base: string | null | undefined, changed: string | null | undefined) => diffWords(base || "", changed || "");
/** 界面复验 N7：表格冲突不显示管道符原文，写“表格 · N 处单元格不同”，引导去并排对比。 */
const tableOf = (item: ConflictItem) => isTable(item.theirs?.markdown || "") || isTable(item.mine?.markdown || "");
const cellDiffs = (item: ConflictItem, side: "theirs" | "mine") => tableCellDiffs(item.base?.markdown || "", (side === "theirs" ? item.theirs?.markdown : item.mine?.markdown) || "");
const initial = (name: string | undefined) => (name || "?").slice(0, 1);
/** 对照稿：按钮行“保留我的 / 使用对方的 / 手动合并（主按钮）”，主操作放最后。 */
const allowed = (item: ConflictItem) => actionsFor(item).filter(c => !mineEmptied(item) || c.action === "use_theirs" || c.action === "manual");
const ordered = (item: ConflictItem) => { const all = allowed(item); return [...all.filter(c => c.action !== "manual"), ...all.filter(c => c.action === "manual")]; };
const theirName = (item: ConflictItem) => whoOf(item);
async function compareMerge(item: ConflictItem, markdown: string) {
  await apply(item, { action: "manual", label: "采用合并结果" }, markdown);
  if (!cardError.value[id(item)]) comparing.value = null;
}
function compareEdit(item: ConflictItem, markdown: string) {
  manualOpen.value = { ...manualOpen.value, [id(item)]: markdown };
  comparing.value = null;
}
async function compareAction(item: ConflictItem, action: "keep_mine" | "use_theirs") {
  comparing.value = null;
  await apply(item, action === "keep_mine" ? { action, label: "保留我的" } : { action, label: "使用对方的版本" });
}

function openManual(item: ConflictItem) {
  manualOpen.value = { ...manualOpen.value, [id(item)]: item.theirs?.markdown || item.mine?.markdown || "" };
}
function insertMine(item: ConflictItem) {
  manualOpen.value = { ...manualOpen.value, [id(item)]: `${manualOpen.value[id(item)] || ""}\n\n${mineText(item)}`.trim() };
}
async function downloadCopy(item: ConflictItem) {
  try {
    const copy = await getConflictCopy(props.documentId, item.conflictId);
    // 方案 C：下载的是最新的我的版本（含冲突后续写）
    const original = item.mine?.markdown || "", latest = mineText(item);
    const text = tab.value === "mine" && original && latest !== original && copy.mineMarkdown.includes(original) ? copy.mineMarkdown.replace(original, () => latest) : copy.mineMarkdown;
    const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
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
    <header class="phead">
      <h2>冲突</h2><span class="sub">{{ phase === 'ready' && !items.length ? "没有待处理" : `${items.length} 处待处理` }}</span>
      <button type="button" class="subtle icon-btn" aria-label="收起冲突面板" @click="emit('close')"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg></button>
    </header>
    <nav v-if="canViewMembers" class="tabs" role="tablist" aria-label="冲突范围">
      <button type="button" role="tab" :aria-selected="tab === 'mine'" :class="{ on: tab === 'mine' }" @click="tab = 'mine'">我的冲突<span v-if="mineCount" class="count">{{ mineCount }}</span></button>
      <button type="button" role="tab" :aria-selected="tab === 'members'" :class="{ on: tab === 'members' }" @click="tab = 'members'">成员冲突<span v-if="memberCount" class="count">{{ memberCount }}</span></button>
    </nav>

    <div class="pbody">
      <p class="pnote" role="note"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12.5l5 5L20 6.5" /></svg><span>冲突内容已单独保存在服务器，不会丢失。其他段落的修改已正常保存。</span></p>
      <template v-if="phase === 'loading'"><div class="ccard skeleton" aria-hidden="true" /><div class="ccard skeleton" aria-hidden="true" /><p class="sr-only" role="status">正在加载冲突…</p></template>
      <div v-else-if="phase === 'error'" class="conflict-empty"><p role="alert">冲突列表加载失败：{{ loadError }}</p><button type="button" class="ghost sm" @click="load">重试</button></div>
      <p v-else-if="empty" class="conflict-empty" role="status">没有待处理的冲突</p>
      <article v-for="item in items" v-else :key="id(item)" class="ccard" :data-conflict="id(item)" tabindex="-1" :aria-busy="busyKey === id(item)" :aria-label="`${REASON_TEXT[item.reason]}：${locationText(item.location)}`">
        <p v-if="cardNotice[id(item)]" class="ccard-notice" role="status">{{ cardNotice[id(item)] }}</p>
        <div class="chead">
          <div class="ctitle"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l9.5 16.5h-19z" /><path d="M12 10v4.5M12 17.5v.01" /></svg><strong>{{ REASON_TEXT[item.reason] }}</strong><time v-if="whenOf(item)">{{ whenOf(item) }}</time></div>
          <div class="cloc"><span>{{ locationText(item.location) }}<span v-if="approxKeys?.includes(id(item))" class="approx">（大致）</span></span><button type="button" class="text" data-testid="conflict-locate" @click="emit('locate', item)">定位</button></div>
          <p v-if="tab === 'members'" class="cdesc">提交人：{{ item.actor?.name || "未记录" }}（只读，仅本人可以处理）</p>
          <p v-if="item.reason === 'DELETED' && item.theirs?.deleted" class="cdesc">{{ whoOf(item) }} 于 {{ whenOf(item) }} 删除了这一段，但你修改过它。</p>
          <p v-else-if="item.reason === 'DELETED'" class="cdesc">你删除了这一段，但 {{ whoOf(item) }} 修改过它。</p>
          <p v-else-if="item.reason === 'FORMAT'" class="cdesc">合并后的内容无法写回原 Word，正文保持对方的版本。你可以下载自己的副本后手动处理。</p>
          <p v-else-if="item.reason === 'MODIFIED' && frozenHere(item)" class="cdesc" data-testid="conflict-frozen-desc">{{ whoOf(item) }} 在你编辑期间保存了这一段。你的版本还在编辑区（红框内），可以继续写；处理前不会写入正文。</p>
          <p v-else-if="item.reason === 'MODIFIED'" class="cdesc">{{ whoOf(item) }} 在你编辑期间保存了这一段，两处修改无法自动合并。</p>
          <p v-else-if="item.reason === 'STRUCTURE'" class="cdesc">{{ whoOf(item) }} 拆分或合并了这里的段落，与你的修改重叠。</p>
          <p v-if="tab === 'mine' && !frozenHere(item) && item.mineLatest?.savedAt" class="cdesc" data-testid="conflict-reopen-note">重新打开后，编辑区显示的是正文里的版本；你的版本在这张卡片里。</p>
        </div>
        <section class="cver ccard-side theirs">
          <h4><span class="avatar alt" aria-hidden="true">{{ initial(whoOf(item)) }}</span>{{ item.theirs?.deleted ? `删除前的内容 · ${whoOf(item)} 已删除` : `${whoOf(item)} 的版本 · 已在正文中（其他人看到的是这一版）` }}</h4>
          <p v-if="tableOf(item) && !item.theirs?.deleted" class="ctable">表格 · {{ cellDiffs(item, 'theirs') }} 处单元格不同（相对修改前）<span class="hint">表格差异请用“并排对比”查看。</span></p>
          <pre v-else-if="item.theirs?.deleted || !item.base?.markdown" :class="{ gone: item.theirs?.deleted }">{{ item.theirs?.markdown || "（无）" }}</pre>
          <pre v-else class="diff"><template v-for="(part, n) in diffOf(item.base?.markdown, item.theirs?.markdown)" :key="n"><ins v-if="part.type === 'ins'">{{ part.text }}</ins><del v-else-if="part.type === 'del'">{{ part.text }}</del><template v-else>{{ part.text }}</template></template></pre>
        </section>
        <section class="cver ccard-side mine">
          <h4><span class="avatar" aria-hidden="true">{{ tab === 'members' ? initial(item.actor?.name) : '我' }}</span>{{ tab === 'members' ? `${item.actor?.name || "提交人"} 的版本 · 未写入正文` : frozenHere(item) ? "我的版本 · 在你的编辑区，未写入正文" : "我的版本 · 未写入正文" }}</h4>
          <p v-if="tab === 'mine' && mineStatus(item)" class="mine-status" :class="{ local: live(item) && !live(item)!.saved }" data-testid="conflict-mine-status">{{ mineStatus(item) }}</p>
          <pre v-if="mineEmptied(item)" class="emptied">（你在编辑区删除了这一段的全部文字）</pre>
          <p v-else-if="tableOf(item) && !item.mine?.deleted" class="ctable">表格 · {{ cellDiffs(item, 'mine') }} 处单元格不同（相对修改前）</p>
          <pre v-else-if="item.mine?.deleted || !item.base?.markdown">{{ item.mine?.deleted ? "（我删除了这一段）" : mineText(item) }}</pre>
          <pre v-else class="diff"><template v-for="(part, n) in diffOf(item.base?.markdown, mineText(item))" :key="n"><ins v-if="part.type === 'ins'">{{ part.text }}</ins><del v-else-if="part.type === 'del'">{{ part.text }}</del><template v-else>{{ part.text }}</template></template></pre>
        </section>
        <details class="cbase ccard-base"><summary><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>查看修改前的原内容</summary><pre>{{ item.base?.markdown || "（无）" }}</pre></details>

        <div class="cacts">
          <div v-if="manualOpen[id(item)] !== undefined" class="ccard-manual">
            <label class="flabel" :for="`manual-${id(item)}`">合并后的内容</label>
            <textarea :id="`manual-${id(item)}`" v-model="manualOpen[id(item)]" class="control" rows="6" :aria-invalid="!manualOpen[id(item)]?.trim()" :aria-describedby="`manual-hint-${id(item)}`" />
            <span v-if="!manualOpen[id(item)]?.trim()" :id="`manual-hint-${id(item)}`" class="field-error" role="alert">内容不能为空；如不需要这段内容，请使用对方的版本。</span>
            <div class="row">
              <button type="button" class="ghost sm" @click="insertMine(item)">插入我的版本</button>
              <button type="button" class="ghost sm" @click="delete manualOpen[id(item)]">取消</button>
              <button type="button" class="primary sm" :disabled="!manualOpen[id(item)]?.trim() || Boolean(busyKey)" @click="apply(item, { action: 'manual', label: '采用合并结果' }, manualOpen[id(item)])">{{ busyKey === id(item) ? "正在处理…" : "采用合并结果" }}</button>
            </div>
          </div>
          <div v-else-if="confirmTheirs === item" class="ccard-confirm" role="alertdialog" aria-label="使用对方的版本">
            <p>{{ theirsConfirm(item) }}</p>
            <div class="row"><button type="button" class="ghost sm" @click="confirmTheirs = null">返回</button><button type="button" class="primary danger sm" @click="apply(item, { action: 'use_theirs', label: '使用对方的版本' })">使用对方的版本</button></div>
          </div>
          <div v-else-if="tab === 'mine' && canResolve" class="row choices" :style="{ gridTemplateColumns: `repeat(${ordered(item).length === 4 ? 2 : ordered(item).length}, minmax(0, 1fr))` }">
            <template v-for="choice in ordered(item)" :key="choice.label">
              <button v-if="choice.action === 'manual'" type="button" class="primary sm" :disabled="Boolean(busyKey)" @click="openManual(item)">{{ choice.label }}</button>
              <button v-else type="button" :class="[!allowed(item).some(c => c.action === 'manual') && choice === allowed(item)[0] ? 'primary' : 'ghost', 'sm']" :title="choice.hint" :disabled="Boolean(busyKey)" @click="apply(item, choice)">{{ busyKey === id(item) && busyLabel === choice.label ? "正在处理…" : choice.label }}</button>
            </template>
          </div>
          <p v-if="tab === 'mine' && canResolve && ordered(item).some(c => c.hint)" class="choice-hint" data-testid="conflict-table-hint">{{ ordered(item).find(c => c.hint)?.hint }}</p>
          <div class="links">
            <button v-if="item.reason === 'MODIFIED' || item.reason === 'STRUCTURE'" type="button" class="text" data-testid="conflict-compare" @click="comparing = item"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="1.5" /><path d="M12 4.5v15" /></svg>并排对比</button>
            <button type="button" class="text" @click="downloadCopy(item)"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11M7 10.5l5 5 5-5" /><path d="M5 20h14" /></svg>{{ tab === 'members' ? "下载提交人的副本" : "下载我的副本" }}</button>
          </div>
          <p v-if="cardError[id(item)]" class="field-error" role="alert">{{ cardError[id(item)] }}</p>
        </div>
      </article>

      <details v-if="handled.length" class="conflict-handled" :open="handledOpen" @toggle="handledOpen = ($event.target as HTMLDetailsElement).open">
        <summary>已处理（{{ handled.length }}）</summary>
        <ul>
          <li v-for="item in handled" :key="id(item)">
            <span>{{ locationText(item.location) }} · {{ ACTION_DONE_TEXT[item.action || ''] || item.action }}<template v-if="item.resolvedAt"> · {{ shortTime(item.resolvedAt) }}</template></span>
            <button type="button" class="text" @click="downloadCopy(item)">{{ tab === 'members' ? "下载提交人的副本" : "下载我的副本" }}</button>
          </li>
        </ul>
      </details>
      <p v-if="items.length" class="hint">处理后正文立即更新，并记录到版本记录中。不处理也可以离开文档，冲突会一直保留，下次打开时继续处理。</p>
    </div>
    <ConflictCompareDialog v-if="comparing" :item="withLatest(comparing)" :theirs-name="theirName(comparing)" :mine-label="tab === 'members' ? `${comparing.actor?.name || '提交人'} 的版本` : undefined" :can-resolve="tab === 'mine' && canResolve" :busy="Boolean(busyKey)"
      @close="comparing = null" @merge="compareMerge(comparing!, $event)" @edit="compareEdit(comparing!, $event)" @keep-mine="compareAction(comparing!, 'keep_mine')" @use-theirs="compareAction(comparing!, 'use_theirs')" />
  </aside>
</template>

<style scoped>
/* 第 7 步：对照稿 .phead/.pnote/.ccard（chead/cver/cbase/cacts）/.tabs */
.conflict-panel { display:flex; flex-direction:column; min-height:0; min-width:0; background:var(--bg-surface); border-left:1px solid var(--border-default); font-size:var(--text-sm); }
.ico { width:var(--icon); height:var(--icon); }
.phead { display:flex; align-items:center; gap:var(--space-2); height:var(--etool-h); flex:none; padding:0 var(--space-2) 0 var(--space-4); border-bottom:1px solid var(--border-subtle); }
.phead h2 { margin:0; font-size:var(--text-md); font-weight:var(--weight-semibold); }
.phead .sub { color:var(--text-tertiary); font-size:var(--text-xs); }
.icon-btn { width:var(--control-h-sm); height:var(--control-h-sm); padding:0; margin-left:auto; }
.tabs { display:flex; gap:var(--space-5); flex:none; padding:0 var(--space-4); border-bottom:1px solid var(--border-subtle); }
.tabs button { display:inline-flex; align-items:center; gap:6px; height:38px; padding:0; border:0; border-bottom:2px solid transparent; background:transparent; color:var(--text-secondary); font-family:var(--font); font-size:var(--text-sm); cursor:pointer; }
.tabs button.on { color:var(--text-primary); font-weight:var(--weight-medium); border-color:var(--brand-600); }
.tabs button.on .count { background:var(--danger-600); color:var(--text-on-brand); }
.tabs button:focus-visible { outline:var(--focus-outline); outline-offset:2px; }
.pbody { flex:1; min-height:0; overflow:auto; display:grid; align-content:start; gap:var(--space-4); padding:var(--space-4); }
.pnote { display:flex; gap:var(--space-2); margin:0; padding:10px var(--space-3); border-radius:var(--radius-control); background:var(--gray-50); color:var(--text-secondary); font-size:var(--text-xs); line-height:var(--leading-xs); }
.pnote .ico { width:var(--icon-sm); height:var(--icon-sm); flex:none; margin-top:2px; color:var(--success-600); }
.conflict-empty { margin:0; color:var(--text-tertiary); text-align:center; padding:var(--space-5) 0; }
.ccard { border:1px solid var(--border-default); border-radius:var(--radius-sheet); overflow:hidden; background:var(--bg-surface); }
.ccard:focus { outline:none; border-color:var(--danger-200); box-shadow:0 0 0 3px var(--danger-50); }
.ccard:focus-visible { border-color:var(--danger-600); }
.ccard.skeleton { min-height:calc(var(--space-7) * 3); background:var(--bg-subtle); }
.ccard-notice { margin:0; padding:var(--space-2) 14px; background:var(--warning-50); color:var(--warning-600); font-size:var(--text-xs); }
.chead { display:grid; gap:var(--space-1); padding:var(--space-3) 14px 10px; border-bottom:1px solid var(--border-subtle); }
.ctitle { display:flex; align-items:center; gap:6px; font-size:var(--text-sm); }
.ctitle strong { font-weight:var(--weight-semibold); }
.ctitle .ico { width:15px; height:15px; color:var(--danger-600); }
.ctitle time { margin-left:auto; color:var(--text-tertiary); font-size:var(--text-xs); }
.cloc { display:flex; justify-content:space-between; align-items:baseline; gap:var(--space-2); color:var(--text-secondary); font-size:var(--text-xs); line-height:var(--leading-xs); overflow-wrap:anywhere; }
.cloc .approx { color:var(--text-tertiary); }
.cloc .text { font-size:var(--text-xs); flex:none; }
.cdesc { margin:0; color:var(--text-secondary); font-size:var(--text-xs); line-height:var(--leading-xs); }
.cver { padding:10px 14px; border-bottom:1px solid var(--border-subtle); }
.cver h4 { display:flex; align-items:center; gap:6px; margin:0 0 6px; color:var(--text-secondary); font-size:var(--text-xs); font-weight:var(--weight-medium); }
.cver .avatar { width:var(--avatar-sm); height:var(--avatar-sm); font-size:var(--text-xs); } /* 终验 S-9：不写死字号 */
.cver .avatar.alt { background:var(--gray-150); color:var(--gray-700); }
.cver pre { margin:0; max-height:calc(var(--space-7) * 5); overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; font-family:var(--font-doc); font-size:var(--text-sm); line-height:1.7; color:var(--text-primary); }
.cver pre.gone { text-decoration:line-through; color:var(--text-tertiary); }
.cver pre.emptied { color:var(--text-tertiary); font-style:italic; }
.mine-status { margin:0 0 var(--space-1); color:var(--text-secondary); font-size:var(--text-xs); line-height:var(--leading-xs); }
.mine-status.local { color:var(--warning-700, var(--text-secondary)); }
.cver ins { background:var(--diff-ins-bg); text-decoration:none; border-radius:2px; padding:0 1px; }
.cver del { background:var(--diff-del-bg); color:var(--danger-700); border-radius:2px; padding:0 1px; }
.ctable { display:grid; gap:2px; margin:0; color:var(--text-primary); }
.cbase { padding:var(--space-2) 14px; border-bottom:1px solid var(--border-subtle); color:var(--text-secondary); font-size:var(--text-xs); }
.cbase summary { display:flex; align-items:center; gap:var(--space-1); cursor:pointer; list-style:none; }
.cbase summary::-webkit-details-marker { display:none; }
.cbase summary .ico { width:var(--icon-sm); height:var(--icon-sm); transition:transform var(--duration) var(--ease-out); }
.cbase[open] summary .ico { transform:rotate(90deg); }
.cbase summary:focus-visible { outline:var(--focus-outline); }
.cbase pre { margin:var(--space-2) 0 0; white-space:pre-wrap; overflow-wrap:anywhere; font-family:var(--font-doc); font-size:var(--text-sm); line-height:1.7; color:var(--text-secondary); }
.cacts { display:grid; gap:10px; padding:var(--space-3) 14px; }
.cacts .row { display:flex; flex-wrap:wrap; gap:var(--space-2); }
.cacts .row > * { flex:1; }
.cacts .row.choices { display:grid; }
.choice-hint { margin:0; font-size:var(--text-xs); line-height:var(--leading-xs); color:var(--text-secondary); }
.links { display:flex; gap:var(--space-4); }
.links .text { display:inline-flex; align-items:center; gap:var(--space-1); font-size:var(--text-xs); }
.links .ico { width:var(--icon-sm); height:var(--icon-sm); }
.ccard-manual { display:grid; gap:var(--space-2); }
.ccard-manual textarea { width:100%; resize:vertical; }
.flabel { font-weight:var(--weight-medium); }
.ccard-confirm p { margin:0 0 var(--space-2); color:var(--text-secondary); font-size:var(--text-xs); }
.field-error { margin:0; color:var(--danger-700); font-size:var(--text-xs); }
.hint { margin:0; color:var(--text-tertiary); font-size:var(--text-xs); line-height:var(--leading-xs); }
.conflict-handled summary { cursor:pointer; color:var(--text-secondary); }
.conflict-handled summary:focus-visible { outline:var(--focus-outline); }
.conflict-handled ul { list-style:none; margin:var(--space-2) 0 0; padding:0; display:grid; gap:var(--space-2); }
.conflict-handled li { display:flex; justify-content:space-between; gap:var(--space-2); overflow-wrap:anywhere; font-size:var(--text-xs); }
.sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); }
</style>
