from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


def build_todo_markdown(plan: dict[str, Any]) -> str:
    """Build a structured Markdown CD to-do checklist from an analyzed Spotify playlist plan."""
    playlist = plan.get("playlist") or {}
    items = plan.get("items") or []
    missing = plan.get("missing_references") or []
    summary = plan.get("summary") or {}

    flagged_items = [it for it in items if it.get("verification", {}).get("status") == "flagged"]
    unavailable_items = [
        it
        for it in items
        if it.get("verification", {}).get("status") != "flagged" and not it.get("saavn_match")
    ]
    version_warning_items = [
        it
        for it in items
        if it.get("verification", {}).get("status") == "warning"
        and it.get("verification", {}).get("warnings")
    ]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        f"# CD To-Do List — {playlist.get('name', 'Spotify Playlist')}",
        "",
        f"- **Spotify Playlist:** {playlist.get('url', 'N/A')}",
        f"- **Generated:** {now}",
        f"- **Reference Crosscheck:** `{plan.get('reference_mode', 'none')}`",
        f"- **Ready to Download on JioSaavn:** {summary.get('ready_to_download', 0)} / {summary.get('total_tracks', len(items))}",
        f"- **Flagged (Covers / Karaoke / Wrong Artist):** {len(flagged_items)}",
        f"- **Missing Official Themes:** {len(missing)}",
        f"- **Unavailable on JioSaavn (Verified Originals):** {len(unavailable_items)}",
        "",
    ]

    if flagged_items:
        lines.extend(
            [
                "## 1. Flagged Tracks (Disabled by Default — Cover / Karaoke / Wrong Artist)",
                "",
                "These tracks on the Spotify playlist did **not** pass original-artist verification and were disabled from downloading by default. Grab the original recordings from your CDs:",
                "",
            ]
        )
        for it in flagged_items:
            tr = it.get("track") or {}
            ver = it.get("verification") or {}
            ref = ver.get("matched_reference")
            flags = "; ".join(ver.get("flags") or [])
            if ref:
                target = f"**{ref['label']} — {ref['title']}** by **{ref['artist']}**"
                ep = f" *(Episodes {ref['episodes']})*" if ref.get("episodes") else ""
                lines.append(
                    f"- [ ] {target}{ep}\n"
                    f"  - Playlist #{tr.get('position')}: `{tr.get('title')}` — `{', '.join(tr.get('artists') or [])}`\n"
                    f"  - Reason flagged: {flags}"
                )
            else:
                lines.append(
                    f"- [ ] **Playlist #{tr.get('position')} — {tr.get('title')}** by **{', '.join(tr.get('artists') or [])}**\n"
                    f"  - Reason flagged: {flags}"
                )
        lines.append("")

    if missing:
        lines.extend(
            [
                "## 2. Missing Official Themes (Not in Spotify Playlist)",
                "",
                "These official songs were found in the authoritative list but are missing completely from the Spotify playlist:",
                "",
            ]
        )
        for ref in missing:
            ep = f" *(Episodes {ref['episodes']})*" if ref.get("episodes") else ""
            lines.append(
                f"- [ ] **{ref['label']} — {ref['title']}** by **{ref['artist']}**{ep}"
            )
        lines.append("")

    if unavailable_items:
        lines.extend(
            [
                "## 3. Verified Originals Unavailable on JioSaavn",
                "",
                "These tracks are verified original releases on Spotify, but no matching official recording was found on JioSaavn (either not in JioSaavn's catalog or geo-restricted to India):",
                "",
            ]
        )
        for it in unavailable_items:
            tr = it.get("track") or {}
            ver = it.get("verification") or {}
            ref = ver.get("matched_reference")
            ref_tag = f"[{ref['label']}] " if ref else ""
            album = tr.get("album_name") or "Unknown Album"
            rel = f" ({tr.get('release_date')})" if tr.get("release_date") else ""
            note = it.get("saavn_note") or "Not found on JioSaavn"
            lines.append(
                f"- [ ] {ref_tag}**{tr.get('title')}** — **{', '.join(tr.get('artists') or [])}**\n"
                f"  - Single/Album: `{album}`{rel} ({tr.get('album_total_tracks') or '?'} tracks)\n"
                f"  - JioSaavn status: {note}"
            )
        lines.append("")

    if version_warning_items:
        lines.extend(
            [
                "## 4. Alternate Version Notes (Original Artist, Non-Original Edit)",
                "",
                "These tracks are by the original artist, but Spotify has an alternate or re-recorded edition. Check your CDs if you want the exact original single release:",
                "",
            ]
        )
        for it in version_warning_items:
            tr = it.get("track") or {}
            ver = it.get("verification") or {}
            ref = ver.get("matched_reference")
            ref_tag = f"[{ref['label']}] " if ref else ""
            warns = "; ".join(ver.get("warnings") or [])
            lines.append(
                f"- [ ] {ref_tag}**{tr.get('title')}** — **{', '.join(tr.get('artists') or [])}** ({warns})"
            )
        lines.append("")

    if not flagged_items and not missing and not unavailable_items:
        lines.append("All tracks were verified and matched on JioSaavn. No manual CD rips needed!")
        lines.append("")

    return "\n".join(lines)


def export_todo_and_plan(output_dir: str, plan: dict[str, Any]) -> tuple[str, str]:
    """Write CD_TODO_LIST.md and download_plan.json into output_dir."""
    os.makedirs(output_dir, exist_ok=True)
    todo_path = os.path.join(output_dir, "CD_TODO_LIST.md")
    plan_path = os.path.join(output_dir, "download_plan.json")

    md = build_todo_markdown(plan)
    with open(todo_path, "w", encoding="utf-8") as fh:
        fh.write(md)

    with open(plan_path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=2)

    return todo_path, plan_path
