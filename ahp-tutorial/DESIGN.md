# 层次分析法 · 15 秒教学视频

## Style Prompt
1920×1080 横屏中文教学短片。沿用用户指定的项目视觉语言：冷白纸面、深蓝文字、蓝色重点，Microsoft YaHei UI。文字逐层出现，图形随着叙事建立。四幕依次采用左右分栏层次图、矩阵与大数字、权重条与检验带、结论与排序图；不重复同一版式。

## Colors
全部取自 ../web/src/styles/base.css，并复制为 tokens.css，源网页不改动。
- --desk: #f4f6f8，背景
- --sheet: #ffffff，图表面板
- --ink: #182534，主文字
- --stamp: #2876d7，图形强调
- --stamp-press: #1d5fb0，蓝色文字

## Typography
只用 var(--font)，Microsoft YaHei UI。视频字级由 var(--text-md) 的倍数推导，正文至少 28px，主标题 80–144px。间距使用现有 space 变量的倍数，圆角使用现有 radius 变量。

## Motion
15 秒，30fps。场景在 0、3.6、7.1、11.2 秒进入。主转场为 0.5 秒水平推移，第三幕采用纵向推移。每幕至少三种缓动与不同的文字/图表入口；先标题、后关系、再解释。转场前不退场、不淡空。最后保留结论到视频结束。

## What NOT to Do
- 不用紫色、渐变、霓虹、模板卡片阵列。
- 不使用重复的居中标题加正文版式。
- 不堆满长句或让文字越界。
- 不把示例数据包装成真实评估结果。

## Teaching example
供应商评估，准则为质量、成本、交付。互反判断矩阵 [[1,2,6],[1/2,1,3],[1/6,1/3,1]]，权重 [0.6,0.3,0.1]，完全一致，CR=0。方案甲在三准则下的局部优先度为 [0.7,0.4,0.5]，方案乙为 [0.3,0.6,0.5]，综合优先度 0.59 与 0.41。

## Content references
- [A web-based software for group decision with analytic hierarchy process](https://pmc.ncbi.nlm.nih.gov/articles/PMC10372897/)
- [Esri: Calculate the consistency ratio for pairwise comparison](https://doc.esri.com/en/arcgis-pro/latest/tool-reference/analysis/calculate-the-consistency-ratio.html)

15 秒为方法概览；CR<0.10 是常用接受准则，不是准确性保证。
