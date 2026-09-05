"""Reconcile prepared current-snapshot and retirement evidence with incidents.

The incident owner supplies shared rows, availability/window authority and current
matching, text/time and epoch callbacks. Reconciliation mutates the supplied
incidents and retains the original shallow evidence sharing. It performs no
file/home/configuration access, clock sampling or provider calls.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


def reconcile_current_snapshot_incidents(
    incidents: List[Dict[str, Any]],
    *,
    identity_snapshot_available: bool,
    active_remote_components: List[Dict[str, Any]],
    snapshot_incident_evidence: List[Dict[str, Any]],
    safety: Dict[str, Any],
    component_is_related_to_selected_window: Callable[[Dict[str, Any]], bool],
    component_matches_identity: Callable[[Dict[str, Any], Dict[str, Any]], bool],
    fromtimestamp: Callable[[int], datetime],
    get_event_time: Callable[[Dict[str, Any]], Optional[datetime]],
    dt_text: Callable[[datetime], str],
    short: Callable[[Any, int], str],
) -> None:
    """Enrich or append incidents using the supplied snapshot and retirement evidence."""
    def evidence_values(
        value: Dict[str, Any],
        plural: str,
        singular: str = "",
    ) -> set[str]:
        supplied = value.get(plural) or []
        if not isinstance(supplied, (list, tuple, set)):
            supplied = [supplied]
        else:
            supplied = list(supplied)
        if singular and value.get(singular):
            supplied.append(value[singular])
        return {str(item).strip() for item in supplied if str(item).strip()}

    def evidence_blocker_kinds(value: Dict[str, Any]) -> set[str]:
        return evidence_values(value, "blocker_kinds", "blocker_kind")

    def retirement_exchange_matches_component(
        ledger: Dict[str, Any],
        retirement: Dict[str, Any],
    ) -> bool:
        if (
            str(ledger.get("ledger_state") or "")
            not in {"exchange_staged", "exchange_committed"}
            or "retirement_ledger" not in evidence_blocker_kinds(ledger)
            or "receipt_retirement" not in evidence_blocker_kinds(retirement)
        ):
            return False
        ledger_sources = evidence_values(ledger, "retirement_source_basenames")
        retirement_sources = evidence_values(
            retirement, "retirement_source_basenames"
        )
        if (
            len(ledger_sources) != 1
            or len(retirement_sources) != 1
            or ledger_sources != retirement_sources
        ):
            return False

        def non_conflicting(plural: str, singular: str = "") -> bool:
            return len(
                evidence_values(ledger, plural, singular)
                | evidence_values(retirement, plural, singular)
            ) <= 1

        expected_or_bound_hashes = (
            evidence_values(ledger, "retirement_expected_sha256s")
            | evidence_values(retirement, "retirement_expected_sha256s")
            | evidence_values(ledger, "source_receipt_sha256s")
            | evidence_values(retirement, "source_receipt_sha256s")
        )
        return bool(
            non_conflicting("receipt_roles")
            and len(expected_or_bound_hashes) <= 1
            and non_conflicting("retirement_source_identity_sha256s")
            and non_conflicting("transaction_ids", "transaction_id")
            and non_conflicting("target_ids", "target_id")
            and non_conflicting("lanes", "lane")
            and non_conflicting("source_receipt_sha256s")
        )

    def retirement_incident_summary(value: Dict[str, Any]) -> str:
        sources = sorted(
            evidence_values(value, "retirement_source_basenames")
        )
        role_labels = sorted(evidence_values(value, "receipt_role_labels"))
        expected = sorted(
            evidence_values(value, "retirement_expected_sha256s")
        )
        phases = sorted(evidence_values(value, "retirement_phases"))
        invalid = sorted(evidence_values(value, "invalid_artifact_names"))
        summary = (
            "Interrupted exact receipt retirement: source receipt "
            f"{', '.join(sources) or 'unavailable'}, role "
            f"{', '.join(role_labels) or 'unavailable'}, expected SHA-256 "
            f"{expected[0] if len(expected) == 1 else 'unavailable'}, "
            "observed retirement phases "
            f"{', '.join(phases) or 'unavailable'}"
        )
        if invalid:
            summary += "; malformed or unreadable artefacts " + ", ".join(
                invalid
            )
        ledger_state = str(value.get("ledger_state") or "")
        if ledger_state in {"exchange_staged", "exchange_committed"}:
            summary += "; blocking ledger exchange state " + ledger_state
        conflict_reasons = sorted(
            evidence_values(value, "retirement_conflict_reasons")
        )
        if conflict_reasons:
            summary += "; conflicting snapshot evidence " + ", ".join(
                conflict_reasons
            )
        return short(summary, 600)

    def merge_retirement_evidence(
        incident: Dict[str, Any],
        evidence: Dict[str, Any],
    ) -> None:
        if evidence.get("blocker_kind") == "receipt_retirement":
            incident["signature"] = evidence["signature"]
        for field, incoming in evidence.items():
            if isinstance(incoming, list) and field != "retirement_source_identities":
                existing = incident.get(field)
                if not isinstance(existing, list):
                    existing = []
                incident[field] = sorted(set(existing) | set(incoming))
        if not incident.get("retirement_source_identities"):
            incident["retirement_source_identities"] = list(
                evidence.get("retirement_source_identities") or []
            )
        kinds = evidence_blocker_kinds(incident) | evidence_blocker_kinds(evidence)
        incident["blocker_kinds"] = sorted(kinds)
        for field in (
            "ledger_state",
            "ledger_sequence",
            "ledger_detail",
            "ledger_record_sha256",
            "selected_window_relationship",
            "current_health_relationship",
        ):
            if field in evidence and evidence.get(field) not in {None, ""}:
                incident[field] = evidence[field]
        artifact_names = sorted(
            evidence_values(incident, "active_artifact_names")
            | evidence_values(incident, "artifact_names")
            | evidence_values(evidence, "artifact_names")
        )
        incident["active_artifact_names"] = artifact_names
        incident["active_artifact_count"] = len(artifact_names)
        incident["summary"] = retirement_incident_summary(incident)

    def evidence_matches_incident(
        evidence: Dict[str, Any],
        incident: Dict[str, Any],
    ) -> bool:
        if evidence.get("category") != incident.get("category"):
            return False
        blocker_kind = str(evidence.get("blocker_kind") or "")
        if (
            blocker_kind == "retirement_ledger"
            and str(evidence.get("ledger_state") or "")
            in {"exchange_staged", "exchange_committed"}
        ):
            return evidence.get("signature") == incident.get("signature")
        if (
            blocker_kind == "receipt_retirement"
            and "receipt_retirement" in evidence_blocker_kinds(incident)
            and (
                evidence.get("retirement_snapshot_conflict") is True
                or incident.get("retirement_snapshot_conflict") is True
            )
        ):
            return evidence.get("signature") == incident.get("signature")
        if (
            evidence.get("signature") == incident.get("signature")
            or component_matches_identity(evidence, incident)
        ):
            return True
        incident_summary = str(incident.get("summary") or "").lower()
        if blocker_kind == "ambiguity_marker":
            hashes = evidence.get("document_sha256s") or evidence.get("artifact_sha256s") or []
            return any(str(value).lower() in incident_summary for value in hashes)
        if blocker_kind == "protocol_activation":
            return True
        if blocker_kind == "transport_inspection":
            return "transport" in incident_summary and "journal" in incident_summary
        if blocker_kind == "media_inspection":
            return "media" in incident_summary and "receipt" in incident_summary
        if blocker_kind == "retirement_ledger":
            sources = evidence.get("retirement_source_basenames") or []
            detail = str(evidence.get("ledger_detail") or "").lower()
            return len(sources) == 1 and sources[0].lower() in incident_summary and detail in incident_summary
        if blocker_kind == "receipt_retirement":
            sources = evidence.get("retirement_source_basenames") or []
            expected = evidence.get("retirement_expected_sha256s") or []
            return len(sources) == len(expected) == 1 and sources[0].lower() in incident_summary and expected[0] in incident_summary
        return False

    if identity_snapshot_available:
        snapshot_candidates: List[Dict[str, Any]] = []
        for component in active_remote_components:
            if not component_is_related_to_selected_window(component):
                continue
            kinds = set(component.get("artifact_kinds") or [])
            roles = set(component.get("receipt_roles") or [])
            states = set(component.get("transaction_states") or [])
            invalid = component.get("invalid_artifact_names") or []
            hashes = (
                component.get("document_sha256s")
                or component.get("artifact_sha256s")
                or []
            )
            names = component.get("artifact_names") or []
            sources = component.get("retirement_source_basenames") or []
            expected = component.get("retirement_expected_sha256s") or []
            transactions = component.get("transaction_ids") or []
            targets = component.get("target_ids") or []
            lanes = component.get("lanes") or []
            fallback = hashlib.sha256(
                "|".join([*hashes, *names, *(component.get("inspection_error_identities") or [])]).encode("utf-8")
            ).hexdigest()
            retirement_fallback = hashlib.sha256(
                json.dumps(
                    {
                        "artifact_names": sorted(
                            component.get("retirement_auxiliary_names") or names
                        ),
                        "inspection_error_identities": sorted(
                            component.get("inspection_error_identities") or []
                        ),
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if "ambiguity_marker" in kinds:
                category, kind = "remote_write_ambiguity_barrier", "ambiguity_marker"
                identity = hashes[0] if len(hashes) == 1 else fallback
                signature = "snapshot:ambiguity-marker:" + identity
                summary = (
                    "Active remote-write ambiguity marker: transaction "
                    f"{', '.join(transactions) or 'unavailable'}, lane {', '.join(lanes) or 'unavailable'}, "
                    f"target {', '.join(targets) or 'unavailable'}, marker/document SHA-256 "
                    f"{identity[:16]}, artefacts {', '.join(names)}"
                )
            elif "receipt_retirement_auxiliary" in kinds:
                category, kind = "remote_write_transaction_barrier", "receipt_retirement"
                source = sources[0] if len(sources) == 1 else "unavailable"
                source_identity_sha256s = component.get(
                    "retirement_source_identity_sha256s"
                ) or []
                identity = (
                    expected[0]
                    if len(expected) == 1
                    else source_identity_sha256s[0]
                    if len(source_identity_sha256s) == 1
                    else retirement_fallback
                )
                signature = f"snapshot:receipt-retirement:{source}:{identity}"
                if component.get("retirement_snapshot_conflict") is True:
                    signature += ":conflict:" + retirement_fallback
                summary = ""
            elif invalid and "conversational_confirmed_reply" in roles:
                category, kind = "conversational_reply_receipt_barrier", "invalid_confirmed_reply_receipt"
                signature = "snapshot:confirmed-reply-receipt:" + fallback
                summary = "Invalid conversational confirmed-reply receipt blocks remote writes: " + ", ".join(names)
            elif invalid or states & {"invalid", "incomplete_pair", "directory_unavailable"}:
                category, kind = "remote_write_transaction_barrier", "invalid_transaction_artifact"
                signature = "snapshot:transaction:" + transactions[0] if len(transactions) == 1 else "snapshot:target:" + targets[0] if len(targets) == 1 else "snapshot:artifacts:" + fallback
                summary = (
                    "Active invalid remote-write transaction barrier: transaction "
                    f"{', '.join(transactions) or 'unavailable'}, lane {', '.join(lanes) or 'unavailable'}, "
                    f"target {', '.join(targets) or 'unavailable'}"
                )
            else:
                continue
            candidate = {
                **component,
                "category": category,
                "blocker_kind": kind,
                "blocker_kinds": [kind],
                "signature": signature,
                "summary": short(summary, 300),
            }
            if kind == "receipt_retirement":
                candidate["summary"] = retirement_incident_summary(candidate)
            snapshot_candidates.append(candidate)
        for evidence in snapshot_incident_evidence:
            if not isinstance(evidence, dict) or not component_is_related_to_selected_window(evidence):
                continue
            blocker = dict(evidence)
            if str(blocker.get("ledger_state") or "") in {
                "exchange_staged",
                "exchange_committed",
            }:
                matches = [
                    item
                    for item in snapshot_candidates
                    if retirement_exchange_matches_component(blocker, item)
                ]
                if len(matches) == 1:
                    merge_retirement_evidence(matches[0], blocker)
                    continue
            snapshot_candidates.append(blocker)
        for evidence in snapshot_candidates:
            for singular, plural in (("transaction_id", "transaction_ids"),
                                     ("target_id", "target_ids"), ("lane", "lanes")):
                evidence.setdefault(plural, [evidence[singular]] if evidence.get(singular) else [])
            matches = [item for item in incidents if evidence_matches_incident(evidence, item)]
            if matches:
                for item in matches:
                    if evidence.get("blocker_kind") == "receipt_retirement":
                        merge_retirement_evidence(item, evidence)
                    else:
                        names = sorted(
                            evidence_values(item, "active_artifact_names")
                            | evidence_values(evidence, "artifact_names")
                        )
                        item["active_artifact_names"] = names
                        item["active_artifact_count"] = len(names)
                continue
            epoch = evidence.get("recorded_at_epoch")
            observed = fromtimestamp(epoch) if type(epoch) is int else get_event_time({"time": str(safety.get("observed_at") or "")})
            if observed is None:
                continue
            transaction_ids = evidence.get("transaction_ids") or []
            target_ids = evidence.get("target_ids") or []
            lanes = evidence.get("lanes") or []
            artifact_names = sorted(
                evidence_values(evidence, "active_artifact_names")
                | evidence_values(evidence, "artifact_names")
            )
            incident = {
                **evidence,
                "category": evidence["category"], "signature": evidence["signature"],
                "status": "current_unresolved", "first_seen": dt_text(observed),
                "last_seen": dt_text(observed), "record_count": 0, "traceback_count": 0,
                "affected_locations": ["current filesystem snapshot"], "summary": evidence["summary"],
                "resolution_reason": "", "resolution_time": None,
                "transaction_id": transaction_ids[0] if len(transaction_ids) == 1 else "",
                "target_id": target_ids[0] if len(target_ids) == 1 else "",
                "lane": lanes[0] if len(lanes) == 1 else "",
                "active_artifact_names": artifact_names,
                "active_artifact_count": len(artifact_names),
                "blocker_kinds": sorted(evidence_blocker_kinds(evidence)),
                "snapshot_only": True,
            }
            incidents.append(incident)
