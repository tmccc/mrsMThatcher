"""Score quotation/image matches using explicit coordinator dependencies.

The bot retains configuration and passes its current helpers on each call.
This module performs no I/O and does not retain callbacks or caller data.
"""

from __future__ import annotations

from collections.abc import Callable


def normalise_tag(
    value: object,
    *,
    re_sub: Callable[..., str],
) -> str:
    """Normalise tag."""
    return re_sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def as_string_list(value: object) -> list[str]:
    """Return the as string list."""
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if value is None:
        return []
    return [str(value)]


def meaningful_tokens(
    value: object,
    *,
    re_findall: Callable[..., list[str]],
    token_stopwords: set[str],
) -> set[str]:
    """Return the meaningful tokens."""
    words = re_findall(r"[a-z0-9]+", str(value or "").lower())
    return {word for word in words if len(word) >= 4 and word not in token_stopwords}


def phrase_matches_text(
    phrase: str,
    text: str,
    *,
    meaningful_tokens: Callable[[object], set[str]],
) -> bool:
    """Return whether phrase matches text."""
    phrase_tokens = meaningful_tokens(phrase)
    if not phrase_tokens:
        return False
    text_tokens = meaningful_tokens(text)
    if len(phrase_tokens) <= 2:
        required = len(phrase_tokens)
    else:
        required = max(
            1,
            min(len(phrase_tokens), int(round(len(phrase_tokens) * 0.65))),
        )
    return len(phrase_tokens & text_tokens) >= required


def hard_mismatch_tokens(
    value: object,
    *,
    meaningful_tokens: Callable[[object], set[str]],
) -> set[str]:
    """Return the hard mismatch tokens."""
    tokens = meaningful_tokens(value)
    normalised: set[str] = set()
    for token in tokens:
        if token.endswith("s") and len(token) > 4:
            normalised.add(token[:-1])
        else:
            normalised.add(token)
    return normalised


def hard_mismatch_phrase_matches_text(
    phrase: str,
    text: str,
    *,
    hard_mismatch_tokens: Callable[[object], set[str]],
) -> bool:
    """Return whether hard mismatch phrase matches text."""
    phrase_tokens = hard_mismatch_tokens(phrase)
    if not phrase_tokens:
        return False
    text_tokens = hard_mismatch_tokens(text)
    if len(phrase_tokens) <= 2:
        required = len(phrase_tokens)
    else:
        required = int((len(phrase_tokens) * 65 + 99) // 100)
    return len(phrase_tokens & text_tokens) >= required


def image_text_corpus(
    image_analysis: dict,
    *,
    as_string_list: Callable[[object], list[str]],
) -> str:
    """Return the image text corpus."""
    parts: list[str] = []
    for key in ("description", "scene_summary"):
        if image_analysis.get(key):
            parts.append(str(image_analysis.get(key)))
    for key in ("visible_elements", "themes", "scene_types"):
        parts.extend(as_string_list(image_analysis.get(key)))
    historical = image_analysis.get("historical_context", {})
    if isinstance(historical, dict):
        parts.extend(as_string_list(historical.get("visible_symbols")))
        if historical.get("event_or_context_hint"):
            parts.append(str(historical.get("event_or_context_hint")))
    return " ".join(parts)


def build_image_topic_idf(
    image_analysis: dict | None,
    *,
    as_string_list: Callable[[object], list[str]],
    normalise_tag: Callable[[object], str],
) -> dict[str, float]:
    """Build image topic idf."""
    if not isinstance(image_analysis, dict):
        return {}
    docs: list[set[str]] = []
    for item in (image_analysis.get("items") or {}).values():
        analysis = item.get("analysis") if isinstance(item, dict) else None
        if not isinstance(analysis, dict):
            continue
        pairing = analysis.get("pairing", {}) if isinstance(analysis.get("pairing"), dict) else {}
        tags = set()
        for value in as_string_list(pairing.get("best_for_topics")) + as_string_list(analysis.get("themes")):
            tag = normalise_tag(value)
            if tag:
                tags.add(tag)
        docs.append(tags)
    total = max(1, len(docs))
    df: dict[str, int] = {}
    for doc in docs:
        for tag in doc:
            df[tag] = df.get(tag, 0) + 1
    return {tag: 1.0 + (total / (count + 1)) ** 0.5 for tag, count in df.items()}


def visual_energy_score(quote_energy: str, image_energy: str) -> float:
    """Return the visual energy score."""
    order = {"low": 0, "medium": 1, "high": 2}
    if quote_energy not in order or image_energy not in order:
        return 0.0
    distance = abs(order[quote_energy] - order[image_energy])
    if distance == 0:
        return 8.0
    if distance == 1:
        return 2.0
    return -8.0


def image_is_out_of_season(
    image_analysis: dict,
    today_mm_dd: str,
    *,
    normalise_tag: Callable[[object], str],
    as_string_list: Callable[[object], list[str]],
    mm_dd_in_window: Callable[[str, str, str], bool],
) -> bool:
    """Return whether image is out of season."""
    seasonality = image_analysis.get("seasonality", {})
    if not isinstance(seasonality, dict) or not seasonality.get("avoid_outside_season_or_occasion"):
        return False
    occasions = {normalise_tag(value) for value in as_string_list(seasonality.get("occasions"))}
    visible_season = normalise_tag(seasonality.get("visible_season"))
    if "christmas" in occasions:
        return not mm_dd_in_window(today_mm_dd, "12-10", "12-28")
    if visible_season == "winter":
        return not mm_dd_in_window(today_mm_dd, "12-01", "02-28")
    if visible_season == "spring":
        return not mm_dd_in_window(today_mm_dd, "03-01", "05-31")
    if visible_season == "summer":
        return not mm_dd_in_window(today_mm_dd, "06-01", "08-31")
    if visible_season == "autumn":
        return not mm_dd_in_window(today_mm_dd, "09-01", "11-30")
    return False


def score_image_for_quote(
    quote_analysis: dict | None,
    image_analysis: dict | None,
    idf: dict[str, float] | None = None,
    *,
    normalise_tag: Callable[[object], str],
    as_string_list: Callable[[object], list[str]],
    visual_energy_score: Callable[[str, str], float],
    image_text_corpus: Callable[[dict], str],
    phrase_matches_text: Callable[[str, str], bool],
    hard_mismatch_phrase_matches_text: Callable[[str, str], bool],
    strong_mismatch_penalty: float,
) -> tuple[float, dict[str, float], bool]:
    """Calculate the production image score and component breakdown for a quotation."""
    if not isinstance(quote_analysis, dict) or not isinstance(image_analysis, dict):
        return 0.0, {"fallback": 0.0}, True

    idf = idf or {}
    components: dict[str, float] = {}
    total = 0.0

    pairing = image_analysis.get("pairing", {}) if isinstance(image_analysis.get("pairing"), dict) else {}
    people = image_analysis.get("people", {}) if isinstance(image_analysis.get("people"), dict) else {}
    historical = image_analysis.get("historical_context", {}) if isinstance(image_analysis.get("historical_context"), dict) else {}
    prefs = quote_analysis.get("archive_image_preferences", {}) if isinstance(quote_analysis.get("archive_image_preferences"), dict) else {}

    image_best_topics = {normalise_tag(value) for value in as_string_list(pairing.get("best_for_topics"))}
    image_themes = {normalise_tag(value) for value in as_string_list(image_analysis.get("themes"))}
    image_weak_topics = {normalise_tag(value) for value in as_string_list(pairing.get("weak_for_topics"))}

    topic_score = 0.0
    for value in as_string_list(quote_analysis.get("primary_topics")):
        tag = normalise_tag(value)
        weight = idf.get(tag, 1.0)
        if tag in image_best_topics:
            topic_score += 5.0 * weight
        if tag in image_themes:
            topic_score += 7.0 * weight
        if tag in image_weak_topics:
            topic_score -= 5.0 * weight
    for value in as_string_list(quote_analysis.get("secondary_topics")):
        tag = normalise_tag(value)
        weight = idf.get(tag, 1.0)
        if tag in image_best_topics:
            topic_score += 2.5 * weight
        if tag in image_themes:
            topic_score += 3.5 * weight
        if tag in image_weak_topics:
            topic_score -= 3.0 * weight
    components["topics"] = topic_score
    total += topic_score

    quote_tones = {normalise_tag(value) for value in as_string_list(quote_analysis.get("tone"))}
    image_tones = {normalise_tag(value) for value in as_string_list(image_analysis.get("tone"))}
    best_tones = {normalise_tag(value) for value in as_string_list(pairing.get("best_for_tones"))}
    image_moods = {normalise_tag(value) for value in as_string_list(people.get("primary_subject_moods"))}
    preferred_moods = {normalise_tag(value) for value in as_string_list(prefs.get("preferred_subject_moods"))}
    tone_score = 3.0 * len(quote_tones & best_tones) + 2.0 * len(quote_tones & image_tones) + 2.0 * len(preferred_moods & image_moods)
    components["tone_mood"] = tone_score
    total += tone_score

    energy_score = visual_energy_score(str(quote_analysis.get("visual_energy", "")), str(image_analysis.get("visual_energy", "")))
    components["visual_energy"] = energy_score
    total += energy_score

    text_corpus = image_text_corpus(image_analysis)
    scene_score = 0.0
    image_scenes = {normalise_tag(value) for value in as_string_list(image_analysis.get("scene_types"))}
    image_activities = {normalise_tag(value) for value in as_string_list(people.get("primary_subject_activities"))}
    image_symbols = {normalise_tag(value) for value in as_string_list(historical.get("visible_symbols")) + as_string_list(image_analysis.get("visible_elements"))}
    for value in as_string_list(prefs.get("preferred_scenes")):
        tag = normalise_tag(value)
        if tag in image_scenes:
            scene_score += 6.0
        elif phrase_matches_text(value, text_corpus):
            scene_score += 2.0
    for value in as_string_list(prefs.get("preferred_activities")):
        if normalise_tag(value) in image_activities or phrase_matches_text(value, text_corpus):
            scene_score += 4.0
    for value in as_string_list(prefs.get("preferred_visible_symbols")) + as_string_list(prefs.get("visual_affinities")):
        if normalise_tag(value) in image_symbols or phrase_matches_text(value, text_corpus):
            scene_score += 4.0
    components["scene_activity_symbols"] = scene_score
    total += scene_score

    historical_score = 0.0
    qhist = quote_analysis.get("historical_context", {}) if isinstance(quote_analysis.get("historical_context"), dict) else {}
    refs = (
        as_string_list(qhist.get("referenced_events"))
        + as_string_list(qhist.get("referenced_people"))
        + as_string_list(qhist.get("referenced_places"))
    )
    matched_ref = any(phrase_matches_text(ref, text_corpus) for ref in refs)
    if matched_ref:
        historical_score += 14.0
    if qhist.get("needs_historical_image_match") and not matched_ref and str(historical.get("specificity", "general")) == "general":
        historical_score -= 4.0
    components["historical"] = historical_score
    total += historical_score

    mismatch_score = 0.0
    for phrase in as_string_list(prefs.get("strong_visual_mismatches")):
        if hard_mismatch_phrase_matches_text(phrase, text_corpus):
            components["strong_mismatch"] = strong_mismatch_penalty
            return strong_mismatch_penalty, components, False
    for phrase in as_string_list(prefs.get("weak_visual_mismatches")):
        if phrase_matches_text(phrase, text_corpus):
            mismatch_score -= 5.0
    components["mismatches"] = mismatch_score
    total += mismatch_score

    quality = image_analysis.get("quality", {}) if isinstance(image_analysis.get("quality"), dict) else {}
    quality_values = []
    for key in ("overall", "crop_suitability_for_x"):
        try:
            quality_values.append(float(quality.get(key, 50)))
        except Exception:
            pass
    for key in ("general_reusability", "semantic_specificity"):
        try:
            quality_values.append(float(pairing.get(key, 50)))
        except Exception:
            pass
    quality_score = ((sum(quality_values) / len(quality_values)) - 50.0) / 50.0 * 4.0 if quality_values else 0.0
    components["quality"] = quality_score
    total += quality_score

    return total, components, True
