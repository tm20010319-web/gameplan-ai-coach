from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Heroes(Model):
    ally_visible: list[str] = Field(default_factory=list, max_length=5)
    enemy_visible: list[str] = Field(default_factory=list, max_length=5)
    enemy_missing: list[str] = Field(default_factory=list, max_length=5)
    confidence: float = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def consistent_visibility(self):
        for roster in (self.ally_visible, self.enemy_visible, self.enemy_missing):
            if len(roster) != len(set(roster)) or any(not name.strip() or len(name) > 20 for name in roster):
                raise ValueError("可见 / 消失英雄必须唯一且名称有效")
        if set(self.enemy_visible) & set(self.enemy_missing):
            raise ValueError("同一敌方英雄不能同时可见和消失")
        return self


class Arena(Model):
    ally_count_near_mid: int | None = Field(default=None, ge=0, le=5)
    enemy_count_near_mid: int | None = Field(default=None, ge=0, le=5)
    ally_low_hp_count: int | None = Field(default=None, ge=0, le=5)
    enemy_low_hp_count: int | None = Field(default=None, ge=0, le=5)
    next_objective: str | None = Field(default=None, max_length=20)
    objective_eta_sec: int | None = Field(default=None, ge=0, le=3600)
    lane_state: Literal["unknown", "mid_pushed_ally", "mid_pushed_enemy", "even"] = "unknown"
    key_skills_ready: bool | None = None
    core_present: bool | None = None
    position_safe: bool | None = None
    safe_trade_available: bool = False
    overextended: bool = False
    confidence: float = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def valid_counts(self):
        if self.ally_count_near_mid is not None and self.ally_low_hp_count is not None:
            if self.ally_low_hp_count > self.ally_count_near_mid:
                raise ValueError("己方低血量人数不能大于附近己方人数")
        return self


class Combat(Model):
    heroes_closing_distance: bool = False
    damage_exchange: bool = False
    skills_or_ults_visible: bool = False


class GameState(Model):
    phase: Literal["bp", "loading", "in_game", "result", "unknown"] = "unknown"
    time_sec: int | None = Field(default=None, ge=0, le=14400)
    heroes: Heroes = Field(default_factory=Heroes)
    map: Arena = Field(default_factory=Arena)
    combat_signal: Combat = Field(default_factory=Combat)


class MatchRequest(Model):
    match_id: str = Field(default="demo", pattern=r"^[a-zA-Z0-9_-]{1,64}$")


class BPRequest(MatchRequest):
    enemy_heroes: list[str] = Field(default_factory=list, max_length=5)
    ally_heroes: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def unique_roster(self):
        combined = self.enemy_heroes + self.ally_heroes
        if any(not hero.strip() or len(hero) > 20 for hero in combined):
            raise ValueError("英雄名称无效")
        if len(combined) != len(set(combined)):
            raise ValueError("标准对局双方英雄不能重复")
        return self


class StateRequest(MatchRequest):
    state_json: GameState
    source: Literal["manual", "vision", "replay"] = "manual"
    recent_advice: dict | None = None


class ObservationRequest(Model):
    observed_action: Literal["engage", "kite", "retreat", "trade", "push_lane", "convert_resource", "unknown"]
    observed_at: int = Field(ge=0, le=14400)
    action_confidence: float = Field(default=1, ge=0, le=1)
    outcome: str = Field(default="未记录", max_length=160)


class SkillTimerRequest(Model):
    side: Literal["enemy", "ally"] = "enemy"
    hero: str = Field(min_length=1, max_length=20)
    slot: int = Field(ge=1, le=4)
    mode: Literal["cast", "remaining"] = "cast"
    seconds: float = Field(gt=0, le=3600, allow_inf_nan=False)
    elapsed_s: float = Field(default=0, ge=0, le=3600, allow_inf_nan=False)
    game_time_s: float | None = Field(default=None, ge=0, le=14400, allow_inf_nan=False)

    @model_validator(mode="after")
    def remaining_has_no_cast_time(self):
        if self.mode == "remaining" and (self.elapsed_s != 0 or self.game_time_s is not None):
            raise ValueError("只登记剩余时间时不能填写施放时间")
        return self


class ResultData(Model):
    result: Literal["胜利", "失败", "未知"] = "未知"
    hero: str = Field(default="", max_length=20)
    kda: str = Field(default="未提供", pattern=r"^(未提供|\d{1,3}/\d{1,3}/\d{1,3})$")
    towers: int | None = Field(default=None, ge=0, le=30)
    objectives: int | None = Field(default=None, ge=0, le=50)
    damage: int | None = Field(default=None, ge=0, le=10000000)
    damage_taken: int | None = Field(default=None, ge=0, le=10000000)
    gold: int | None = Field(default=None, ge=0, le=1000000)
    participation: float | None = Field(default=None, ge=0, le=100)


class ReviewRequest(MatchRequest):
    result: ResultData
    advice_records: list[dict] = Field(default_factory=list, max_length=100)


class ROI(Model):
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def in_bounds(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("ROI 超出图像范围")
        return self


MAX_VISION_IMAGE_BASE64 = 12000000


class RecentVisionFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # A current lossless PNG becomes a history frame on the next observation.
    image_base64: str = Field(max_length=MAX_VISION_IMAGE_BASE64)
    captured_at: float = Field(gt=0, allow_inf_nan=False)
    panel_candidate: bool = False


class VisionRequest(MatchRequest):
    image_base64: str = Field(max_length=MAX_VISION_IMAGE_BASE64)
    model: str | None = Field(default=None, max_length=100)
    roi: ROI | None = None
    captured_at: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    input_kind: Literal["live", "image", "video", "sample"] = "image"
    video_time_s: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    all_frames: bool = False
    focus: Literal["full", "heroes", "skills"] = "full"
    # Normal monitoring sends a short history. Event-driven replay may send a
    # dense burst (up to 32 frames) so a blink/ultimate onset is not hidden
    # between two 200ms samples.
    recent_frames: list[RecentVisionFrame] = Field(default_factory=list, max_length=31)
    perspective_side: Literal["neutral", "a", "b"] = "neutral"

    @model_validator(mode="after")
    def ordered_frames(self):
        if self.all_frames and (self.input_kind != "video" or self.video_time_s is None or len(self.recent_frames) > 5):
            raise ValueError("完整视频识别每批最多 6 帧，且必须提供录像时间")
        if self.video_time_s is not None and self.input_kind != 'video':
            raise ValueError('录像时间只适用于视频')
        if self.recent_frames:
            times = [frame.captured_at for frame in self.recent_frames]
            if self.captured_at is None or any(a >= b for a, b in zip(times, times[1:] + [self.captured_at])):
                raise ValueError("连续画面必须按采集时间递增")
            older = [frame for frame in self.recent_frames if self.captured_at-frame.captured_at > 8]
            if older and (self.focus != 'skills' or self.all_frames or len(older) > 1
                          or not older[0].panel_candidate or self.captured_at-times[0] > 15):
                raise ValueError("只接受最近 8 秒的连续画面，另可保留一张最近 15 秒的战绩候选")
        return self


class HeroObservation(BaseModel):
    """Small, mandatory identity-only output for continuous observation."""
    model_config = ConfigDict(extra="forbid")
    phase: Literal["bp", "loading", "in_game", "result", "not_game", "unknown"]
    player_hero: str | None = Field(max_length=20)
    ally_roster: list[str] = Field(max_length=5)
    enemy_roster: list[str] = Field(max_length=5)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


class LoadingPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allies: list[str] = Field(min_length=1, max_length=5)
    enemies: list[str] = Field(min_length=1, max_length=5)
    player: str | None = Field(default=None, max_length=20)
    source: Literal["manual", "vision_draft", "sample"] = "manual"

    @model_validator(mode="after")
    def valid_lineup(self):
        for roster in (self.allies, self.enemies):
            if len(set(roster)) != len(roster) or any(not re_name.strip() or re_name != re_name.strip() or len(re_name) > 20 for re_name in roster):
                raise ValueError("阵容中英雄名必须有效且不能重复")
        if set(self.allies) & set(self.enemies):
            raise ValueError("当前 5v5 方案不支持双方重复英雄，请核对阵容")
        if self.player and self.player not in self.allies:
            raise ValueError("所选操控英雄不在当前查看方")
        return self


class BPPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allies: list[str] = Field(default_factory=list, max_length=5)
    enemies: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def valid_rosters(self):
        combined = self.allies + self.enemies
        if any(not hero.strip() or hero != hero.strip() or len(hero) > 20 for hero in combined):
            raise ValueError("英雄名称无效")
        if len(set(combined)) != len(combined):
            raise ValueError("双方英雄不能重复")
        return self


class ScreenCaptureRequest(Model):
    source_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    roi: ROI | None = None


class PictureFrameRequest(ScreenCaptureRequest):
    roi: ROI


class PictureAnalysisRequest(VisionRequest):
    side: Literal["neutral", "a", "b"] = "neutral"
    player: str | None = Field(default=None, max_length=20)
    revision: int = Field(default=0, ge=0, le=1000000000)
    lineup: LoadingPlanRequest | None = None
    lane: Literal["unknown", "对抗路", "中路", "发育路", "打野", "辅助"] = "unknown"


class PictureRevisionRequest(MatchRequest):
    revision: int = Field(ge=0, le=1000000000)


class PersonalPlanRequest(MatchRequest):
    identity_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    revision: int = Field(ge=0, le=1000000000)
    side: Literal['a', 'b']
    player: str = Field(min_length=1, max_length=20)
    lane: Literal['对抗路', '中路', '发育路', '打野', '辅助']


class CoachAnalysisRequest(MatchRequest):
    frame_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    side: Literal["neutral", "a", "b"] = "neutral"
    player: str | None = Field(default=None, max_length=20)
    lineup: LoadingPlanRequest | None = None
    lane: Literal['unknown', '对抗路', '中路', '发育路', '打野', '辅助'] = 'unknown'


class ScreenSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: int = Field(ge=1, le=4)
    remaining_s: float | None = Field(default=None, ge=0, le=3600)
    visible_text: str = Field(default="", max_length=40)

    @model_validator(mode="after")
    def require_readable_number(self):
        import re
        if self.remaining_s is not None:
            number = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:s|秒)?\s*", self.visible_text)
            if not number or abs(float(number.group(1)) - self.remaining_s) > 0.01:
                self.remaining_s = None
                self.visible_text = ""
        return self


class HeroLevelObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hero: str = Field(min_length=1, max_length=20)
    level: int = Field(ge=1, le=15, strict=True)
    visible_text: str = Field(min_length=1, max_length=16)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    frame_index: int = Field(ge=0, le=7)

    @model_validator(mode="after")
    def readable_level(self):
        import re
        match = re.fullmatch(r"\s*(?:Lv\.?\s*)?(\d{1,2})\s*(?:级)?\s*", self.visible_text, re.IGNORECASE)
        if not match or int(match.group(1)) != self.level:
            raise ValueError("英雄等级必须与可见数字一致")
        return self


class SummonerObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hero: str = Field(min_length=1, max_length=20)
    skill: Literal["闪现", "惩击", "惩戒", "终结", "狂暴", "疾跑", "治疗术", "眩晕", "晕眩", "净化", "弱化", "干扰", "传送"]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    evidence: str = Field(min_length=1, max_length=160)
    source: Literal["scoreboard_icon", "loading_icon", "selection_icon"]
    frame_index: int = Field(ge=0, le=7)
    template_margin: float | None = Field(default=None,ge=0,le=2,allow_inf_nan=False)
    color_score: float | None = Field(default=None,ge=0,le=1,allow_inf_nan=False)
    smoothed_score: float | None = Field(default=None,ge=0,le=1,allow_inf_nan=False)
    smoothed_margin: float | None = Field(default=None,ge=0,le=2,allow_inf_nan=False)


class SummonerCorrectionRequest(MatchRequest):
    hero: str = Field(min_length=1, max_length=20)
    skill: Literal["闪现", "惩击", "终结", "狂暴", "疾跑", "治疗术", "眩晕", "净化", "弱化", "干扰", "传送"] | None


class EnemyCastObservation(BaseModel):
    """Model evidence, not a trusted cooldown or confirmed event."""
    model_config = ConfigDict(extra="ignore")
    hero: str = Field(min_length=1, max_length=20)
    skill: str = Field(min_length=1, max_length=40)
    slot: int | None = Field(default=None, ge=1, le=5)
    used: bool = Field(strict=True)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    evidence: str = Field(min_length=1, max_length=240)
    event_type: Literal["cast_start", "ongoing", "uncertain", "keyframe"]
    frame_index: int | None = Field(ge=0, le=7)
    onset_start_index: int | None = Field(default=None, ge=0, le=7)
    onset_end_index: int | None = Field(default=None, ge=0, le=7)

    @model_validator(mode='after')
    def onset_window(self):
        if self.event_type == 'keyframe' and (self.onset_start_index is not None or self.onset_end_index is not None):
            raise ValueError('单帧特效计时不使用起手时间范围')
        if (self.onset_start_index is None) != (self.onset_end_index is None):
            raise ValueError('释放时间范围必须同时提供前后索引')
        if self.onset_start_index is not None and not (
                self.frame_index is not None and self.onset_start_index <= self.frame_index <= self.onset_end_index):
            raise ValueError('候选起手必须位于释放时间范围内')
        return self


class SkillObservation(BaseModel):
    """Compact schema for temporal enemy cast recognition."""
    model_config = ConfigDict(extra="forbid")
    phase: Literal["bp", "loading", "in_game", "result", "not_game", "unknown"]
    player_hero: str | None = Field(default=None, max_length=20)
    ally_roster: list[str] = Field(default_factory=list, max_length=5)
    enemy_roster: list[str] = Field(default_factory=list, max_length=5)
    hero_levels: list[HeroLevelObservation] = Field(default_factory=list, max_length=20)
    summoner_skills: list[SummonerObservation] = Field(default_factory=list, max_length=80)
    enemy_skill_events: list[EnemyCastObservation] = Field(default_factory=list, max_length=10)


class ScreenObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: Literal["bp", "loading", "in_game", "result", "not_game", "unknown"]
    game_time_s: int | None = Field(default=None, ge=0, le=14400)
    player_hero: str | None = Field(default=None, max_length=20)
    player_hp_percent: int | None = Field(default=None, ge=0, le=100)
    ally_roster: list[str] = Field(default_factory=list, max_length=5)
    enemy_roster: list[str] = Field(default_factory=list, max_length=5)
    hero_levels: list[HeroLevelObservation] = Field(default_factory=list, max_length=20)
    summoner_skills: list[SummonerObservation] = Field(default_factory=list, max_length=80)
    self_skills: list[ScreenSkill] = Field(default_factory=list, max_length=4)
    enemy_skill_events: list[EnemyCastObservation] = Field(default_factory=list, max_length=10)
    note: str = Field(default="", max_length=180)

    @model_validator(mode="after")
    def valid_slots(self):
        if len({skill.slot for skill in self.self_skills}) != len(self.self_skills):
            raise ValueError("技能槽位不能重复")
        if self.phase != "in_game":
            self.hero_levels = []
            self.enemy_skill_events = []
            self.self_skills = []
            self.player_hp_percent = None
            self.game_time_s = None
        if self.phase not in ("bp", "loading", "in_game"):
            self.summoner_skills = []
        if self.phase not in ("bp", "in_game"):
            self.player_hero = None
        if self.player_hero and (self.player_hero in self.ally_roster) == (self.player_hero in self.enemy_roster):
            self.player_hero = None
        return self




