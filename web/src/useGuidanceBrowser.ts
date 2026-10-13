import { computed, ref, type Ref } from "vue";
import type { WritingGuidanceGroup } from "./api/documents";

export function useGuidanceBrowser(groups: Ref<WritingGuidanceGroup[]>) {
  const search = ref("");
  const groupKey = ref("");
  const itemId = ref("");
  const keyOf = (group: WritingGuidanceGroup) => String(group.headingIndex ?? "body");
  const available = computed(() => groups.value.filter(group => group.items.length));
  const matches = computed(() => {
    const query = search.value.trim().toLocaleLowerCase();
    return available.value.filter(group => `${group.headingPath || ""} ${group.heading}`.toLocaleLowerCase().includes(query));
  });
  const group = computed(() => matches.value.find(value => keyOf(value) === groupKey.value) ?? matches.value[0]);
  const selectedKey = computed({
    get: () => group.value ? keyOf(group.value) : "",
    set: (value: string) => { groupKey.value = value; itemId.value = ""; },
  });
  const item = computed(() => group.value?.items.find(value => value.id === itemId.value) ?? group.value?.items[0]);
  const itemIndex = computed(() => item.value ? group.value!.items.indexOf(item.value) : -1);
  function moveItem(delta: number) {
    const next = group.value?.items[itemIndex.value + delta];
    if (next) itemId.value = next.id;
  }
  function revealItem(id: string) {
    const target = available.value.find(value => value.items.some(item => item.id === id));
    if (!target) return;
    search.value = "";
    groupKey.value = keyOf(target);
    itemId.value = id;
  }
  function reset() { search.value = ""; groupKey.value = ""; itemId.value = ""; }
  return { search, available, matches, group, selectedKey, item, itemIndex, keyOf, moveItem, revealItem, reset };
}
