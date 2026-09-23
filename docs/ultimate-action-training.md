# 第一批敌方大招动作模型

2026-09-19：桌面 `闪现` 的三英雄素材已另建约 6 万参数的轻量识别实验，完成真实训练和训练视频回放。入口见 [三英雄大招识别实验](three-hero-ultimate-prototype.md)。该模型与下述三分类骨架独立，当前结果不代表实战或跨视频准确率。

当前架构把任务拆成两层：Qwen3-VL/本地 OCR 负责确认英雄和敌我归属，轻量时序模型只判断最近画面是否出现大招起手或持续动作。冷却时间仍从 `resources/knowledge/skill_catalog.json` 读取，动作模型不生成冷却数值。

已为两段录屏生成低分辨率抽样数据：

- `work/ultimate-action-dataset/frames.jsonl`：约 4554 个 2 FPS 帧；
- `work/ultimate-action-dataset/candidate-windows.json`：202 个按运动变化筛出的候选窗口；
- `work/ultimate-action-dataset/candidate-sheets/`：带编号和时间范围的联系表；
- `work/ultimate-action-dataset/annotations.json`：待填写的标注副本。

候选窗口只是运动变化候选，不能直接当作大招标签。编辑 `annotations.json`，为每个窗口填写：

- `hero`：已由加载槽位或 Qwen 确认的英雄名；
- `label`：`cast_start`、`ongoing` 或 `background`。

## 全自动流程

无需逐条人工标注，运行：

```powershell
.venv\Scripts\python.exe scripts\pseudo_label_action_candidates.py `
  work\ultimate-action-dataset\annotations.json
```

默认使用本机 `qwen3-vl:8b-instruct`。先用加载/战绩 OCR 核对阵营和昵称，再逐窗口检查 6 帧，排除选人、加载、面板切换，只将可定位的敌方目标送入视觉模型，并对正样本再次局部复核。无法绑定身份、模型冲突、输出错误均保留 `unknown`，不将未知当成负样本。每个窗口处理完即写入文件，重新运行可继续。

- `auto-annotations.json`：所有候选窗口的结果、未知原因、模型原始提议和复核结果；
- `auto-identities.json`：自动读取的阵容及昵称映射；
- `auto-evidence/`：带帧号/录像时刻的图像证据；
- `auto-training.json`：符合当前筛选条件的弱标签训练候选；
- `auto-report.json`：类别、英雄覆盖和处理状态汇总。

这些是自动弱标签，并非人工真值；两次模型判断也不是独立的正确性证明。202 个窗口来自画面运动筛选，未覆盖整段录像的每一秒，不能据此计算整局漏报率。未被采样或未进入候选窗口的瞬时动作可能遗漏。

训练自动筛选出的标签：

```powershell
.venv\Scripts\python.exe scripts\train_ultimate_action_model.py `
  work\ultimate-action-dataset\auto-training.json `
  --out data\models\ultimate_action.pt
```

训练脚本按完整视频划分验证集，至少需要两段视频，每类至少 4 个窗口，且训练侧和留出视频都要有三类标签。样本不足时输出 `.report.json` 并停止，不用未知标签凑数。留出指标仅是与自动标签的一致率，不是实战准确率。

当前 3D CNN 是整屏三分类实验骨架，不输入英雄身份、不定位目标，不能视为已完成逐英雄动作识别。只有时序分类输出，尚不能替代现有事件检测器。模型存在后，可在实时监控中观察诊断输出：

```powershell
$env:ULTIMATE_ACTION_MODEL_PATH = (Resolve-Path data\models\ultimate_action.pt)
```

仅在 `skill_scan.action_model` 中报告模型分数，不修改大招或闪现事件。先前实验性的 `ULTIMATE_ACTION_MODEL_GATE` 已取消，因为整屏类别分数不能确认具体释放者。下一阶段应以目标裁剪、英雄条件输入和跨对局的逐英雄评估替代整屏实验分类器。

现有两段录屏足以建立第一版验证集，但不能证明覆盖所有皮肤、遮挡、视角和团战密集场景。扩展到其他英雄前，应加入新的完整对局，并继续按视频而不是相邻帧切分训练和测试。
