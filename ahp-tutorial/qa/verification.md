# 验收

- 成片为 H.264 + AAC，1920×1080，30fps，450 帧，15.000 秒。
- 已在 http://localhost:5173/ahp-tutorial/index.html 按 1920×1080 截图并查看四幕及三个转场中间帧。
- HyperFrames lint：0 errors / 0 warnings。
- HyperFrames validate：无控制台错误，72 个文字元素通过 WCAG AA。
- HyperFrames inspect：2.8、6.6、10.7、14.6 秒共 4 个关键帧，0 layout issues。
- 已从 MP4 提取并检查四幕联系表 final-contact-sheet.png；音视频轨均为 15 秒，四段中文旁白均存在。
- check.mjs 对判断矩阵、权重与 59% 示例结果做断言，同时截图检测文字尺寸溢出。
- 动画图的 8 个 offscreen 标记来自整幕推移及其标题在转场中的位置；5 个 paced-slow 标记来自进度条和背景装饰；2 个 collision 标记是进度条叠加在整幕推移容器上。均为预期效果，已检查对应画面，无正文意外重叠。
- 内容来源与示例假设见 DESIGN.md。
