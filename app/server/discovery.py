"""Auto-discovery of desktop environment and user scripts for Nixi.

Run via:  uv run nixi-server --discover

Pipeline:
  1. Detect WM from environment variables
  2. fastfetch --format json  →  system profile  →  nixi-profile.toml
  3. Find WM config files (using known paths per WM, confirmed from official docs)
  4. Feed raw config content to LLM → structured {intent: command} extraction
  5. Scan ~/.local/bin and known script dirs by filename stem
  6. Merge: user script > LLM-extracted > silent skip (no fallbacks written)
  7. Write [actions] block into nixi.toml (user section above marker untouched)
  8. Write nixi-profile.toml with system info for LLM context
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Known intents — what Nixi can do
# ---------------------------------------------------------------------------

@dataclass
class IntentSpec:
    description: str
    args: str = ""          # template args appended after script path, e.g. "{path}"
    # Filename stems that strongly suggest a script handles this intent
    filename_hints: list[str] = field(default_factory=list)


INTENTS: dict[str, IntentSpec] = {
    "set_wallpaper": IntentSpec(
        description=(
            "Sets the desktop wallpaper. The path argument is optional — "
            "if the user does not mention a specific file or image name, "
            "leave path empty and the system will automatically pick a random "
            "image from ~/Pictures. Only set path if the user explicitly names "
            "a file or describes a specific image."
        ),
        args="{path}",
        filename_hints=["wallpaper", "setwall", "set-wall", "set_wall",
                        "swww", "feh", "nitrogen", "hyprpaper", "swaybg"],
    ),
    "take_screenshot": IntentSpec(
        description="Takes a screenshot of the current screen.",
        filename_hints=["screenshot", "scrot", "grimblast", "flameshot", "maim"],
    ),
    "volume_up": IntentSpec(
        description="Increases the system volume.",
        filename_hints=["volume-up", "volume_up", "vol-up", "volup", "louder"],
    ),
    "volume_down": IntentSpec(
        description="Decreases the system volume.",
        filename_hints=["volume-down", "volume_down", "vol-down", "voldown"],
    ),
    "mute_audio": IntentSpec(
        description="Mutes or unmutes the system audio.",
        filename_hints=["mute", "unmute", "toggle-mute", "toggle_mute"],
    ),
    "lock_screen": IntentSpec(
        description="Locks the screen.",
        filename_hints=["lockscreen", "lock-screen", "lock_screen",
                        "swaylock", "i3lock", "betterlockscreen"],
    ),
    "open_launcher": IntentSpec(
        description="Opens the application launcher.",
        filename_hints=["launcher", "rofi", "wofi", "dmenu", "fuzzel", "hyprlauncher"],
    ),
    "media_play_pause": IntentSpec(
        description="Plays or pauses the current media player.",
        filename_hints=["playpause", "play-pause", "play_pause"],
    ),
    "media_next": IntentSpec(
        description="Skips to the next track.",
        filename_hints=["next-track", "next_track", "media-next", "nextsong"],
    ),
    "media_prev": IntentSpec(
        description="Goes back to the previous track.",
        filename_hints=["prev-track", "prev_track", "media-prev", "prevsong"],
    ),
    "brightness_up": IntentSpec(
        description="Increases screen brightness.",
        filename_hints=["brightness-up", "brightness_up", "brightnessup"],
    ),
    "brightness_down": IntentSpec(
        description="Decreases screen brightness.",
        filename_hints=["brightness-down", "brightness_down", "brightnessdown"],
    ),
}

# Script directories to scan, in priority order
SCAN_DIRS: list[Path] = [
    Path.home() / ".local" / "bin",
    Path.home() / ".config" / "nixi" / "scripts",
    Path.home() / ".config" / "hypr" / "scripts",
    Path.home() / ".config" / "bspwm" / "scripts",
    Path.home() / ".config" / "sway" / "scripts",
    Path.home() / "scripts",
    Path.home() / "bin",
]

# WM config file candidates — confirmed from official docs.
# Ordered by priority (first existing file wins).
# Sources:
#   Hyprland: https://wiki.hypr.land  (2026: lua is primary, .conf is legacy)
#   BSPWM:    https://wiki.archlinux.org/title/bspwm  (keybinds are in sxhkd)
#   Sway:     https://wiki.archlinux.org/title/sway
#   i3:       https://i3wm.org/docs/userguide.html
WM_CONFIG_CANDIDATES: dict[str, list[Path]] = {
    "hyprland": [
        Path.home() / ".config" / "hypr" / "hyprland.lua",
        Path.home() / ".config" / "hypr" / "hyprland.conf",
        Path.home() / ".config" / "hypr" / "hyprland.config",
    ],
    "bspwm": [
        # sxhkd holds the keybinds for bspwm, bspwmrc holds window rules
        Path.home() / ".config" / "sxhkd" / "sxhkdrc",
        Path.home() / ".config" / "bspwm" / "bspwmrc",
    ],
    "sway": [
        Path.home() / ".config" / "sway" / "config",
        Path.home() / ".sway" / "config",
        Path("/etc/sway/config"),
    ],
    "i3": [
        Path.home() / ".config" / "i3" / "config",
        Path.home() / ".i3" / "config",
        Path("/etc/i3/config"),
    ],
}


# ---------------------------------------------------------------------------
# WM detection
# ---------------------------------------------------------------------------

def detect_wm() -> str:
    """Detect the running WM/compositor from environment variables."""
    # Most reliable: compositor-specific env vars set at runtime
    env_checks = [
        ("HYPRLAND_INSTANCE_SIGNATURE", "hyprland"),
        ("SWAYSOCK",                    "sway"),
        ("I3SOCK",                      "i3"),
    ]
    for var, name in env_checks:
        if os.environ.get(var):
            return name

    desktop = (
        os.environ.get("XDG_CURRENT_DESKTOP", "")
        or os.environ.get("DESKTOP_SESSION", "")
    ).lower()

    for name in ("hyprland", "sway", "bspwm", "i3", "openbox", "xfce", "gnome", "kde"):
        if name in desktop:
            return name

    return "_generic"


# ---------------------------------------------------------------------------
# System profile via fastfetch
# ---------------------------------------------------------------------------

@dataclass
class SystemProfile:
    wm: str = "_generic"
    session_type: str = "unknown"
    os_name: str = ""
    kernel: str = ""
    shell: str = ""
    terminal: str = ""
    packages: int = 0
    displays: list[str] = field(default_factory=list)


def probe_system(wm: str) -> SystemProfile:
    """Run fastfetch --format json and extract relevant fields."""
    profile = SystemProfile(
        wm=wm,
        session_type=os.environ.get("XDG_SESSION_TYPE", "unknown"),
        # Read shell directly from environment — fastfetch sees parent process (uv/python)
        shell=Path(os.environ.get("SHELL", "")).name,
    )

    ff = shutil.which("fastfetch") or shutil.which("neofetch")
    if not ff:
        return profile

    try:
        result = subprocess.run(
            [ff, "--format", "json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return profile
        entries = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return profile

    for entry in entries:
        t = entry.get("type", "")
        r = entry.get("result", {})
        if not isinstance(r, dict):
            continue
        if t == "OS":
            profile.os_name = r.get("prettyName", r.get("name", ""))
        elif t == "Kernel":
            profile.kernel = r.get("release", "")
        elif t == "Terminal":
            profile.terminal = r.get("prettyName", r.get("exeName", ""))
        elif t == "Packages":
            profile.packages = r.get("all", 0)
        elif t == "Display":
            displays_raw = entry.get("result", [])
            if isinstance(displays_raw, list):
                for d in displays_raw:
                    if isinstance(d, dict):
                        w = d.get("width", "?")
                        h = d.get("height", "?")
                        hz = d.get("refreshRate", "?")
                        if w != "?" and h != "?":
                            profile.displays.append(f"{w}x{h}@{hz}Hz")

    return profile


# ---------------------------------------------------------------------------
# WM config reading
# ---------------------------------------------------------------------------

def find_wm_configs(wm: str) -> list[Path]:
    """Return all existing config files for the detected WM."""
    candidates = WM_CONFIG_CANDIDATES.get(wm, [])
    return [p for p in candidates if p.exists()]


def read_wm_configs(wm: str) -> dict[str, str]:
    """
    Return {filename: content} for all found WM config files.
    Content is capped at 12 KB per file to stay within LLM context.
    """
    configs: dict[str, str] = {}
    for path in find_wm_configs(wm):
        try:
            content = path.read_text(errors="ignore")[:12_000]
            configs[str(path)] = content
        except OSError:
            pass
    return configs


# ---------------------------------------------------------------------------
# LLM-based config extraction
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """\
You are a Linux desktop assistant configuration parser and action metadata generator.

The user is running the "{wm}" window manager/compositor.
Below are the raw contents of their WM configuration file(s).

IMPORTANT PARSING NOTES FOR {wm}:
- Commands may be quoted with EITHER single quotes OR double quotes. Parse both.
- In Hyprland lua configs, commands appear as:
    hl.dsp.exec_cmd("command here")
    hl.dsp.exec_cmd('command here')
    hl.exec_cmd("command here")
    hl.exec_cmd('command here')
  The command string is everything between the quotes — including && chains, pipes, $() expansions.
- A variable like `local menu = "hyprlauncher"` followed by `hl.dsp.exec_cmd(menu)` means the command is "hyprlauncher".
- Read the FULL config carefully before responding. Do not stop at first match.

Your job:
1. Find shell commands bound to each action intent listed below.
2. For each found command, generate RICH metadata so a future voice assistant LLM
   knows exactly how to handle user speech and what arguments to extract.

Intents to look for:
- set_wallpaper    : command that sets/changes the desktop wallpaper dynamically
- take_screenshot  : command that captures the screen (area or fullscreen)
- volume_up        : command that raises speaker/audio volume
- volume_down      : command that lowers speaker/audio volume
- mute_audio       : command that mutes/unmutes audio
- lock_screen      : command that locks the screen
- open_launcher    : command that opens an app launcher (rofi, wofi, fuzzel, hyprlauncher, etc.)
- media_play_pause : command that plays or pauses media
- media_next       : command that skips to next track
- media_prev       : command that goes to previous track
- brightness_up    : command that increases screen brightness
- brightness_down  : command that decreases screen brightness

Return ONLY a valid JSON object. Include an intent key ONLY if you find its command.
Structure per intent:
{{
  "intent_name": {{
    "command": "the exact shell command. Use {{amount}} placeholder where a percentage/value should be dynamic. Use {{path}} where a file path should be dynamic.",
    "description": "Rich description for the voice assistant LLM. State: what tool is used, what each arg means, exact format required (e.g. '5%+' not just 'percent'), and what default to use if user does not specify.",
    "args": "{{amount}}" or "{{path}}" or "" (empty string if no dynamic args)
  }}
}}

Rules:
- Replace hardcoded percentages with {{amount}}: e.g. "wpctl ... 5%+" → "wpctl ... {{amount}}"
- For take_screenshot: prefer the area/region selection variant if both exist
- For set_wallpaper: ONLY include if it is a dynamic command. Skip static startup wallpaper lines (e.g. hyprpaper preload with a fixed path).
- For open_launcher: if a variable like `local menu = "hyprlauncher"` is used, resolve it to the actual value.
- Return raw JSON only — no markdown fences, no explanation text whatsoever.

Example output for volume_down:
{{
  "volume_down": {{
    "command": "wpctl set-volume @DEFAULT_AUDIO_SINK@ {{amount}}",
    "description": "Decreases system volume using wpctl (PipeWire). The amount arg must be a percentage string with minus sign, e.g. '5%-' or '20%-'. Default to '5%-' if user does not specify an amount.",
    "args": "{{amount}}"
  }}
}}

--- CONFIG FILES ---
{config_content}
"""


def extract_actions_with_llm(wm: str, configs: dict[str, str]) -> dict[str, dict]:
    """
    Send WM config content to the LLM.
    Returns {intent: {command, description, args}} with rich metadata.
    Returns {} on any failure.
    """
    if not configs:
        return {}

    config_content = "\n\n".join(
        f"=== {path} ===\n{content}"
        for path, content in configs.items()
    )

    prompt = _EXTRACTION_PROMPT.format(wm=wm, config_content=config_content)

    try:
        api_key = os.environ.get("GOOGLE_CLOUD_API_KEY", "").strip()
        if not api_key:
            print("    [warn] GOOGLE_CLOUD_API_KEY not set — skipping LLM extraction")
            return {}

        from google import genai
        from google.genai import types

        client = genai.Client(vertexai=True, api_key=api_key)
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=[types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)],
            )],
            config=types.GenerateContentConfig(
                max_output_tokens=2048,
            ),
        )

        raw = (response.text or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        extracted: dict = json.loads(raw)

        # Validate and normalise — accept both old flat format and new rich format
        result: dict[str, dict] = {}
        for k, v in extracted.items():
            if k not in INTENTS:
                continue
            if isinstance(v, str) and v.strip():
                # Old flat format fallback: wrap it
                result[k] = {"command": v.strip(), "description": "", "args": ""}
            elif isinstance(v, dict) and isinstance(v.get("command"), str) and v["command"].strip():
                result[k] = {
                    "command": v["command"].strip(),
                    "description": str(v.get("description", "")).strip(),
                    "args": str(v.get("args", "")).strip(),
                }
        return result

    except Exception as e:
        print(f"    [warn] LLM extraction failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# Script scanning — filename-stem matching only (no body scan)
# ---------------------------------------------------------------------------

def scan_scripts() -> dict[str, Path]:
    """
    Scan SCAN_DIRS for executable scripts. Match by filename stem to intent hints.
    Returns {intent: first_matching_script_path}. First directory/match wins.
    """
    found: dict[str, Path] = {}
    remaining = set(INTENTS.keys())

    for directory in SCAN_DIRS:
        if not directory.is_dir():
            continue
        for script in sorted(directory.iterdir()):
            if not script.is_file() or not os.access(script, os.X_OK):
                continue
            stem = script.stem.lower().replace("-", "_").replace(" ", "_")
            for intent in list(remaining):
                hints = [h.replace("-", "_") for h in INTENTS[intent].filename_hints]
                if any(hint in stem for hint in hints):
                    found[intent] = script
                    remaining.discard(intent)
        if not remaining:
            break

    return found


# ---------------------------------------------------------------------------
# Merge: script > llm-extracted > silent skip
# ---------------------------------------------------------------------------

@dataclass
class DiscoveredAction:
    intent: str
    command: str
    source: str        # "script" | "wm-config"
    origin: str        # human-readable detail for the comment in nixi.toml
    description: str = ""   # rich description generated by LLM during discovery
    args: str = ""          # template args, e.g. "{amount}" or "{path}"


def build_actions(
    wm: str,
    script_map: dict[str, Path],
    llm_map: dict[str, dict],
) -> list[DiscoveredAction]:
    actions: list[DiscoveredAction] = []

    for intent, spec in INTENTS.items():
        if intent in script_map:
            path = script_map[intent]
            # For user scripts, ask the LLM to generate rich description
            # based on what it knows about the intent + the script name
            llm_meta = llm_map.get(intent, {})
            cmd = str(path)
            args = llm_meta.get("args", spec.args)
            if args:
                cmd = f"{cmd} {args}"
            actions.append(DiscoveredAction(
                intent=intent,
                command=cmd,
                source="script",
                origin=str(path),
                description=llm_meta.get("description", "") or spec.description,
                args=args,
            ))
        elif intent in llm_map:
            meta = llm_map[intent]
            actions.append(DiscoveredAction(
                intent=intent,
                command=meta["command"],
                source="wm-config",
                origin=f"extracted from {wm} config by LLM",
                description=meta.get("description", "") or spec.description,
                args=meta.get("args", ""),
            ))

    return actions


# ---------------------------------------------------------------------------
# TOML generation
# ---------------------------------------------------------------------------

_AUTO_HEADER = "# --- auto-generated by `nixi-server --discover` --- do not edit below this line ---"
_AUTO_FOOTER = "# --- end auto-generated ---"


def _render_action_block(action: DiscoveredAction) -> str:
    safe_cmd = action.command.replace("\\", "\\\\").replace('"', '\\"')
    # Use the rich LLM-generated description if available, else fall back to spec default
    description = action.description or INTENTS[action.intent].description
    safe_desc = description.replace('"', '\\"')
    lines = [
        f"[actions.{action.intent}]",
        f"# source: {action.origin}",
        f'description = "{safe_desc}"',
        f'command = "{safe_cmd}"',
    ]
    return "\n".join(lines)


def _strip_old_auto_section(text: str) -> str:
    if _AUTO_HEADER not in text:
        return text
    before = text[: text.index(_AUTO_HEADER)]
    tail = text[text.index(_AUTO_HEADER):]
    after = tail[tail.index(_AUTO_FOOTER) + len(_AUTO_FOOTER):] if _AUTO_FOOTER in tail else ""
    return (before.rstrip() + "\n" + after.lstrip()).rstrip()


def write_actions_to_config(config_path: Path, actions: list[DiscoveredAction]) -> None:
    """Write only the auto-generated block. User section above the marker is untouched."""
    existing = config_path.read_text() if config_path.exists() else ""
    stripped = _strip_old_auto_section(existing)

    if not actions:
        config_path.write_text(stripped.rstrip() + "\n")
        return

    blocks = "\n\n".join(_render_action_block(a) for a in actions)
    section = f"{_AUTO_HEADER}\n\n{blocks}\n\n{_AUTO_FOOTER}"
    config_path.write_text(stripped.rstrip() + "\n\n" + section + "\n")


# ---------------------------------------------------------------------------
# Wallpaper file discovery
# ---------------------------------------------------------------------------

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}

# Directory names that strongly suggest they are NOT wallpaper folders
_SCREENSHOT_DIR_HINTS = {"screenshot", "screenshots", "screen", "capture", "grab", "clip"}

# Candidate root directories to search for wallpapers, in priority order
_WALLPAPER_SEARCH_ROOTS = [
    Path.home() / "Pictures",
    Path.home() / "Wallpapers",
    Path.home() / "wallpapers",
    Path.home() / "Images",
]


def _is_screenshot_dir(path: Path) -> bool:
    """Return True if a directory name suggests it holds screenshots, not wallpapers."""
    name = path.name.lower()
    return any(hint in name for hint in _SCREENSHOT_DIR_HINTS)


def scan_wallpapers() -> list[str]:
    """
    Scan known picture directories recursively for image files.
    Excludes any subdirectory whose name suggests it holds screenshots.
    Returns a sorted list of absolute path strings.
    """
    found: list[Path] = []

    for root in _WALLPAPER_SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in _IMAGE_EXTENSIONS:
                continue
            # Exclude files inside screenshot-named directories at any depth
            if any(_is_screenshot_dir(parent) for parent in f.parents if parent != root.parent):
                continue
            found.append(f)

    return sorted(str(f) for f in found)

def write_profile(
    profile_path: Path,
    profile: SystemProfile,
    configs: dict[str, str],
    wallpapers: list[str],
) -> None:
    lines = [
        "# Nixi system profile — auto-generated by `nixi-server --discover`",
        "# Gives Nixi context about your setup. Do not edit manually.",
        "",
        "[system]",
        f'wm           = "{profile.wm}"',
        f'session_type = "{profile.session_type}"',
        f'os           = "{profile.os_name}"',
        f'kernel       = "{profile.kernel}"',
        f'shell        = "{profile.shell}"',
        f'terminal     = "{profile.terminal}"',
        f'packages     = {profile.packages}',
    ]
    if profile.displays:
        displays_str = ", ".join(f'"{d}"' for d in profile.displays)
        lines.append(f"displays     = [{displays_str}]")

    if configs:
        lines += ["", "[system.wm_config_files]",
                  "# Config files read during discovery"]
        for path in configs:
            lines.append(f'# {path}')

    if wallpapers:
        lines += [
            "",
            "[wallpapers]",
            "# Discovered image files — screenshots and captures are excluded.",
            "# Nixi picks randomly from this list when no specific wallpaper is requested.",
            "files = [",
        ]
        for w in wallpapers:
            lines.append(f'  "{w}",')
        lines.append("]")

    profile_path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_discovery(config_path: Path) -> None:
    print("Nixi discovery starting...\n")

    # 1. Detect WM
    wm = detect_wm()
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    print(f"  WM / compositor  : {wm}")
    print(f"  Session type     : {session}")

    # 2. System probe via fastfetch
    print("\n  Probing system with fastfetch...")
    profile = probe_system(wm)
    for label, val in [
        ("OS", profile.os_name), ("Kernel", profile.kernel),
        ("Shell", profile.shell), ("Terminal", profile.terminal),
        ("Packages", str(profile.packages) if profile.packages else ""),
        ("Displays", ", ".join(profile.displays)),
    ]:
        if val:
            print(f"    {label:<12}: {val}")

    # 3. Read WM config files
    print(f"\n  Reading {wm} config files...")
    configs = read_wm_configs(wm)
    if configs:
        for path in configs:
            print(f"    {path}")
    else:
        print(f"    No config files found for {wm}")

    # 4. LLM extraction from config
    llm_map: dict[str, str] = {}
    if configs:
        print(f"\n  Extracting actions from config via LLM...")
        llm_map = extract_actions_with_llm(wm, configs)
        if llm_map:
            for intent, meta in llm_map.items():
                cmd_preview = meta.get("command", "")[:65]
                print(f"    {intent:<22} <- {cmd_preview}")
        else:
            print("    Nothing extracted")

    # 5. Script scan
    print(f"\n  Scanning script directories...")
    script_map = scan_scripts()
    for directory in SCAN_DIRS:
        if directory.is_dir():
            print(f"    {directory}  [found]")
    if script_map:
        for intent, path in script_map.items():
            print(f"    {intent:<22} <- {path}")

    # 6. Scan wallpapers
    print(f"\n  Scanning for wallpaper images...")
    wallpapers = scan_wallpapers()
    if wallpapers:
        print(f"    Found {len(wallpapers)} image(s), excluding screenshot directories:")
        for w in wallpapers:
            print(f"    {w}")
    else:
        print("    No images found in ~/Pictures or ~/Wallpapers")

    # 7. Build final action list (script > llm > skip)
    actions = build_actions(wm, script_map, llm_map)

    print(f"\n  Final actions ({len(actions)}):")
    if not actions:
        print("    None — add scripts to ~/.local/bin or ~/.config/nixi/scripts/")
    else:
        for a in actions:
            print(f"    {a.intent:<22} [{a.source:<9}]  {a.origin}")

    # 8. Write nixi.toml [actions] block
    write_actions_to_config(config_path, actions)
    print(f"\n  Actions written  : {config_path}")

    # 9. Write nixi-profile.toml (includes wallpaper file list)
    profile_path = config_path.parent / "nixi-profile.toml"
    write_profile(profile_path, profile, configs, wallpapers)
    print(f"  Profile written  : {profile_path}")

    print("\nDone. Restart nixi-server to load the new config.\n")
