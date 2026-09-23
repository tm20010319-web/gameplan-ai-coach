# 英雄大招增量识别实验

当前已用桌面 `C:\Users\Administrator\Desktop\闪现` 中的吕布、虞姬、貂蝉、亚瑟四段素材训练真实轻量卷积模型，完成连续回放、事件去重和外部知识库基础冷却关联。

结果入口：[可观看的标注回放](../work/three-hero-experiment/report.html)、[中文报告](../work/three-hero-experiment/report.md)、[完整指标](../work/three-hero-experiment/report.json)。权重：`work/three-hero-experiment/hero_ultimate.pt`。不要将此权重交给旧的 `ULTIMATE_ACTION_MODEL_PATH`，两者架构不同。

## 当前结果与边界

- 当前 3 段训练视频回放各识别并记录一个目标大招，没有未匹配或重复事件。此结果基于助手观察标注，未独立复核。
- 每位英雄只有一段视频。238 个标注抽样帧全部参与训练；训练样本拟合为 100% 不代表实战识别率。独立视频测试准确率仍未知，未做随机相邻帧切分来充当独立测试。
- 吕布首次检测约 3.09 秒、虞姬约 2.29 秒、貂蝉从 0 秒可见。吕布/虞姬参考起手约 3.0/2.2 秒，观察标注不确定度约 ±0.15 秒。记录在两个采样连续确认后产生，因此首次检测时间与确认时间分开保存。
- 貂蝉从首帧就有法阵，没有同一来源的背景负例，不能证明模型学到了法阵而非皮肤/地图。其释放时刻保持未知。
- 视频含慢放、循环与其他英雄特效；只评估标注的目标大招，没有全面标注画面所有人的技能。
- 裁剪位置按样本人工设置，以去掉网页、标题、字幕和技能按钮。模型输入不含文件名、英雄标签或录像时间；仍可能记住皮肤、场景和固定位置，暂不具备自动定位、跟踪和敌我判别。
- 模型约 6 万参数，约 242 KiB，支持逐帧流式输入。CPU 模型前向耗时单独测量，不代表完整桌面采集链路性能。尚未启用到正在运行的监控服务。
- 知识库基础冷却：吕布 50 秒、虞姬 25/22.5/20 秒、貂蝉 40/35/30 秒。这些是当前知识库版本的数值，不是这些旧录像中的实测冷却。等级、冷却缩减、冷却起点、敌我和游戏时钟未核对，记录不输出真实剩余秒数。

## 复现

在 `D:\tm_work` 的 PowerShell 中，使用已有安装 PyTorch、OpenCV、NumPy 的项目 Python：

```powershell
.venv\Scripts\python.exe scripts/hero_ultimate_experiment.py scan
.venv\Scripts\python.exe scripts/hero_ultimate_experiment.py train --epochs 100
.venv\Scripts\python.exe scripts/hero_ultimate_experiment.py replay
.venv\Scripts\python.exe scripts/render_hero_ultimate_report.py
```

`scan` 只检查素材清单；`train` 重新训练并回放；`replay` 加载实际保存的权重重新推理。渲染使用本机 FFmpeg/libass，生成 H.264 MP4 和本地 HTML，不启动服务、不联网。`--folder`、`--annotations`、`--out` 可覆盖默认路径。训练与回放都校验视频 SHA-256，变更/缺失素材不静默复用旧标注。

## 追加英雄或视频

标注库采用增量策略：以文件名和 SHA-256 复用已经确认的记录。新文件只新增一条标注；旧视频内容未变化时不重新观察或重写标注。相同文件名的内容发生变化时状态会变为 `changed_needs_review`，避免把旧时间区间误用到新视频。

1. 将新的 MP4 放入桌面 `闪现` 文件夹，保留原有素材，建议使用 `英雄名_大招_序号.mp4` 避免覆盖。
2. 运行 `scan`，新文件会出现在 `inventory.json`，状态为需要检查裁剪和动作标注。新素材不会未经检查就自动变成训练标签。
3. 检查画面并在 `data/ultimate_examples/annotations.json` 增加文件名、文件哈希、归一化裁剪框、可见大招区间、确定不在目标大招动作中的背景区间。未知/模糊边界不标背景；开头已经施放的动作 `onset_s` 填 `null`。普通技能无需分类或计时，只需有一些“不应触发大招记录”的画面。
4. 使用全部已标注素材重新训练，类别从标注的英雄名自动生成，再检查每段回放。不会只用新英雄覆盖训练导致丢失旧类别。
5. 每位英雄有额外来源后，留出完整独立视频用于测试，不参与训练与阈值调整，再评估跨视频召回、误报和时间误差。当前脚本输出始终是训练视频回放指标，没有独立验证集就不报告实战准确率。

## 代码入口

- `gameplan/skills/hero_ultimate_prototype.py`：模型、裁剪、因果时序输入、事件去重和 `StreamingClassifier` 流式接口。
- `scripts/hero_ultimate_experiment.py`：素材扫描、训练、重载回放、事件评估和知识库关联。
- `scripts/render_hero_ultimate_report.py`：模型结果视频与报告。
- `tests/test_hero_ultimate_prototype.py`：瞬时误触发过滤、连续动作去重、重新施放、采样中断、因果性、未知标注和冷却语义。

流式接口要求调用方提供正确裁剪框与递增的采集时间戳。跳转、换视频或新对局必须 `reset()`。接口返回的事件是未知阵营的视觉观察；接入敌方计时前仍需身份绑定、技能等级和游戏时钟校准。
