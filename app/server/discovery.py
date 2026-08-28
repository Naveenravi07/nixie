"""Auto-discovery of desktop environment and user scripts for Nixi.

Run via:  uv run nixi-server --discover

Pipeline:
  1. Detect WM from environment variables
  2. fastfetch --format json  →  system profile  →  nixi-profile.toml
  3. Run tree on ~ and ~/.config (depth 2-3) for a broad directory view
  4. Send tree output to LLM → identifies relevant config files, script dirs, wallpaper dirs
  5. Read discovered config files, feed raw content to LLM → structured {intent: command}
  6. Scan discovered + known script dirs by filename stem
  7. Scan discovered + known wallpaper dirs recursively
  8. Merge: user script > LLM-extracted > silent skip (no fallbacks written)
  9. Write [actions] block into nixi.toml (user section above marker untouched)
  10. Write nixi-profile.toml with system info + wallpaper list
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
                        "wallselect", "wall-sel", "swww", "feh", "nitrogen",
                        "hyprpaper", "swaybg"],
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
        filename_hints=["lockscreen", "lock-screen", "lock_screen", "screenlock",
                        "locker", "swaylock", "i3lock", "betterlockscreen"],
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
# Home directory structure scan via tree
# ---------------------------------------------------------------------------

def tree_home_structure() -> str:
    """
    Run `tree` on ~ and ~/.config (both depth 2) to get a broad view of the
    user's home directory layout. Returns combined tree output.
    Falls back to `find` output if tree is not installed.
    """
    home = Path.home()
    outputs: list[str] = []

    # Directory names that are pure noise (app caches, browser internals, etc.)
    # Filtering them keeps the tree small enough for LLM analysis while
    # preserving meaningful structure (app config dirs, scripts, rices, walls).
    noise = (
        "__pycache__|.git|node_modules|.cache|Cache|GPUCache|Code Cache|"
        "Cached*|Crashpad|DawnGraphiteCache|DawnWebGPUCache|Dictionaries|"
        "Local Storage|logs|Preferences|Session Storage|Shared Dictionary|"
        "shared_proto_db|VideoDecodeStats|Cookies*|DIPS*|machineid|"
        "Trust Tokens*|Opera Vault|blob_storage|app.db|Network|WebStorage|"
        "History*|Visited Links|Favicons*|Login Data*|BrokerCache|"
        "manifest|extensions|IndexedDB|Backups|Default|Profile"
    )

    tree_bin = shutil.which("tree")

    if tree_bin:
        # HOME: directories only (depth 2) — shows layout (Music, Pictures, Code...)
        #       without the noise of every downloaded file. Wallpaper/media files
        #       are found by recursive scanning of the dirs the AI picks.
        # CONFIG: with files (depth 2) — the AI needs file names to decide which
        #       config files to read.
        for depth, label, path, dirs_only in [
            (2, "HOME", home, True),
            (2, "HOME/.config", home / ".config", False),
        ]:
            if not path.is_dir():
                continue
            cmd = [tree_bin, "-L", str(depth), "--dirsfirst", "-I", noise]
            if dirs_only:
                cmd.append("-d")
            cmd.append(str(path))
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=15,
                )
                if result.returncode == 0 and result.stdout.strip():
                    outputs.append(f"=== {label} (depth {depth}) ===\n{result.stdout}")
            except (subprocess.TimeoutExpired, OSError):
                pass
    else:
        # Fallback: use find to get a basic structure
        for depth, label, path in [
            (2, "HOME", home),
            (2, "HOME/.config", home / ".config"),
        ]:
            if not path.is_dir():
                continue
            try:
                result = subprocess.run(
                    ["find", str(path), "-maxdepth", str(depth), "-type", "d"],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode == 0:
                    outputs.append(f"=== {label} (depth {depth}) ===\n{result.stdout}")
            except (subprocess.TimeoutExpired, OSError):
                pass

    return "\n".join(outputs)


# LLM prompt for analyzing directory structure
_STRUCTURE_ANALYSIS_PROMPT = """\
You are a Linux desktop environment analyst. The user is running the "{wm}" window manager.

Below is the output of `tree` showing the structure of their home directory and ~/.config.

Your job: Analyze this directory structure to understand the user's whole setup:
1. **Config files** that likely contain window manager keybindings, shortcuts, or action definitions
2. **Script directories** that likely contain user scripts (volume, brightness, screenshots, etc.)
3. **Wallpaper directories** that likely contain wallpaper images
4. **Media roots** — the top-level directories where the user keeps music, videos and other media

TREE OUTPUT:
{tree_output}

Return ONLY a valid JSON object with these keys:
{{
  "config_files": [
    "list of absolute paths to config files that likely define keybindings/actions",
    "expand ~ to the actual home directory path",
    "include paths like .config/bspwm/bspwmrc, .config/sxhkd/sxhkdrc, .config/hypr/hyprland.conf, etc."
  ],
  "script_dirs": [
    "list of absolute paths to directories that likely contain scripts",
    "prefer directories with names like scripts, bin, or containing executable files"
  ],
  "wallpaper_dirs": [
    "list of absolute paths to directories that likely contain wallpaper images",
    "look for dirs named walls, wallpapers, backgrounds, rice/*/walls, or containing .jpg/.png/.webp files",
    "if you see a rices/ or rice/ directory, include it — wallpapers may be nested inside"
  ],
  "media_roots": [
    "list of absolute top-level home directories for music, videos, pictures, etc.",
    "e.g. /home/user/Music, /home/user/Videos, /home/user/Pictures"
  ]
}}

Rules:
- Expand ~ to the full home directory path ({home_dir})
- Be aggressive — if a directory looks like it could contain relevant files, include it
- For config_files: prefer actual config files over directories. Include files like bspwmrc, sxhkdrc, config.ini, *.conf, *.lua, etc.
- For script_dirs: look for dirs named scripts, bin, or dirs inside .config/{wm}/ that might have helper scripts
- For wallpaper_dirs: look for dirs with names like walls, wallpapers, backgrounds, rices/*/walls
- For media_roots: the obvious top-level dirs from the HOME tree (Music, Videos, Pictures, etc.)
- Only include paths that actually appear in the tree output (or are obvious siblings of what appears)
- Return raw JSON only — no markdown fences, no explanation text
"""


def analyze_structure_with_llm(wm: str, tree_output: str) -> dict[str, list[str]]:
    """
    Send the tree output to the LLM and ask it to identify relevant config files,
    script directories, and wallpaper directories.
    Returns {config_files: [...], script_dirs: [...], wallpaper_dirs: [...]}.
    Returns empty dict on any failure.
    """
    if not tree_output.strip():
        return {}

    prompt = _STRUCTURE_ANALYSIS_PROMPT.format(
        wm=wm,
        tree_output=tree_output[:60_000],  # cap to stay within LLM context
        home_dir=str(Path.home()),
    )

    try:
        api_key = os.environ.get("GOOGLE_CLOUD_API_KEY", "").strip()
        if not api_key:
            print("    [warn] GOOGLE_CLOUD_API_KEY not set — skipping structure analysis")
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

        result: dict = json.loads(raw)

        # Validate and expand ~ in paths
        expanded: dict[str, list[str]] = {}
        for key in ("config_files", "script_dirs", "wallpaper_dirs", "media_roots"):
            paths = result.get(key, [])
            if not isinstance(paths, list):
                continue
            expanded[key] = []
            for p in paths:
                if not isinstance(p, str):
                    continue
                # Expand ~ to home dir
                p = p.replace("~", str(Path.home()))
                expanded[key].append(p)

        return expanded

    except Exception as e:
        print(f"    [warn] Structure analysis failed: {e}")
        return {}


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

def find_wm_configs(wm: str, additional_paths: list[Path] | None = None) -> list[Path]:
    """Return all existing config files for the detected WM.
    Merges hardcoded candidates with LLM-discovered paths."""
    candidates = WM_CONFIG_CANDIDATES.get(wm, [])
    # Add LLM-discovered paths (deduplicated)
    seen: set[str] = set()
    result: list[Path] = []
    for p in list(additional_paths or []) + candidates:
        resolved = str(p)
        if resolved in seen:
            continue
        seen.add(resolved)
        if p.exists():
            result.append(p)
    return result


def read_wm_configs(wm: str, additional_paths: list[Path] | None = None) -> dict[str, str]:
    """
    Return {filename: content} for all found WM config files.
    Merges hardcoded candidates with LLM-discovered paths.
    Content is capped at 12 KB per file to stay within LLM context.
    """
    configs: dict[str, str] = {}
    for path in find_wm_configs(wm, additional_paths):
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
Below are the raw contents of their WM configuration file(s) and any discovered user scripts.

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
- When an intent is handled by a script in the SCRIPTS section:
  - READ the script's code to get the EXACT flags it supports. The command MUST be `<full script path> <the real flags>` — never invent flags.
  - E.g. if the script parses `case $1 in --inc|--dec|--toggle)`, the commands are "…/Volume --inc", "…/Volume --dec", "…/Volume --toggle".
  - If the script steps by a fixed amount (e.g. always +5 or 5%), leave {{amount}} out of the command and explain the fixed step in the description.
  - If the script accepts a dynamic amount/value, use the {{amount}} or {{path}} placeholder with its exact position.
  - Use the ABSOLUTE PATH you were given, exactly as written in the SCRIPTS section.
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
{script_section}
"""


def extract_actions_with_llm(
    wm: str,
    configs: dict[str, str],
    scripts_content: str = "",
) -> dict[str, dict]:
    """
    Send WM config content + discovered script contents to the LLM.
    Returns {intent: {command, description, args}} with rich metadata.
    Returns {} on any failure.
    """
    if not configs and not scripts_content:
        return {}

    config_content = "\n\n".join(
        f"=== {path} ===\n{content}"
        for path, content in configs.items()
    )

    if scripts_content:
        script_section = "\n--- DISCOVERED SCRIPTS (full paths + contents) ---\n" + scripts_content
    else:
        script_section = ""

    prompt = _EXTRACTION_PROMPT.format(
        wm=wm,
        config_content=config_content,
        script_section=script_section,
    )

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

def scan_scripts(additional_dirs: list[Path] | None = None) -> dict[str, Path]:
    """
    Scan directories for executable scripts. Match by filename stem to intent hints.
    Accepts additional_dirs from LLM structure analysis (checked first).
    Returns {intent: first_matching_script_path}. First directory/match wins.
    """
    found: dict[str, Path] = {}
    remaining = set(INTENTS.keys())

    # Merge: LLM-discovered dirs first, then hardcoded fallbacks
    search_dirs: list[Path] = list(additional_dirs or []) + list(SCAN_DIRS)

    for directory in search_dirs:
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


def collect_script_contents(
    script_dirs: list[Path],
    per_file_budget: int = 8_000,
    total_budget: int = 60_000,
) -> str:
    """
    Read the contents of executable scripts in the given dirs so the LLM can
    extract the exact CLI flags instead of guessing from filenames.
    Smallest scripts are prioritised first — intent-relevant scripts (volume,
    brightness, screenshots, lock, media) are typically tiny, while big
    utilities (network managers, full editors) exceed the budget and are dropped.
    Returns a formatted string: "=== path ===\n<content>" blocks.
    """
    files: list[tuple[int, Path]] = []
    for directory in script_dirs:
        if not directory.is_dir():
            continue
        for script in sorted(directory.iterdir()):
            if not script.is_file() or not os.access(script, os.X_OK):
                continue
            try:
                files.append((script.stat().st_size, script))
            except OSError:
                continue

    sections: list[str] = []
    total = 0
    for size, script in sorted(files):
        if total >= total_budget:
            break
        try:
            content = script.read_text(errors="ignore")[:per_file_budget]
        except OSError:
            continue
        sections.append(f"=== {script} ===\n{content}")
        total += len(content)

    return "\n\n".join(sections)


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
            command = meta["command"]
            # If the LLM mapped this intent to a discovered script (full path),
            # treat it as a first-class script action rather than a config parse.
            script_cmd = Path(command)
            if script_cmd.is_file() and os.access(script_cmd, os.X_OK):
                actions.append(DiscoveredAction(
                    intent=intent,
                    command=command,
                    source="script",
                    origin=str(script_cmd),
                    description=meta.get("description", "") or spec.description,
                    args=meta.get("args", ""),
                ))
            else:
                actions.append(DiscoveredAction(
                    intent=intent,
                    command=command,
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


def scan_wallpapers(additional_dirs: list[Path] | None = None) -> list[str]:
    """
    Scan known picture directories recursively for image files.
    Excludes any subdirectory whose name suggests it holds screenshots.
    Accepts additional_dirs from LLM structure analysis.
    Returns a sorted list of absolute path strings.
    """
    found: list[Path] = []
    seen: set[str] = set()

    # Merge hardcoded roots + LLM-discovered dirs (LLM dirs first for priority)
    search_roots: list[Path] = list(additional_dirs or []) + list(_WALLPAPER_SEARCH_ROOTS)

    for root in search_roots:
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
            # Deduplicate across multiple search roots
            resolved = str(f)
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(f)

    return sorted(str(f) for f in found)

def write_profile(
    profile_path: Path,
    profile: SystemProfile,
    configs: dict[str, str],
    wallpapers: list[str],
    wallpaper_dirs: list[str] | None = None,
    media_roots: list[str] | None = None,
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

    # Save machine-discovered paths so the runtime LLM understands the layout
    discovered_dirs = (wallpaper_dirs or []) + (media_roots or [])
    if discovered_dirs:
        lines += ["", "[paths]",
                  "# Directories discovered during discovery (wallpapers, media roots, etc.)"]
        for d in discovered_dirs:
            lines.append(f'# {d}')

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

    # 3. Aggressive directory scan via tree + LLM analysis
    print(f"\n  Scanning home directory structure (tree)...")
    tree_output = tree_home_structure()
    if tree_output:
        tree_lines = tree_output.count("\n")
        print(f"    Captured {tree_lines} lines of directory structure")
    else:
        print("    [warn] tree output empty — falling back to hardcoded paths")

    print(f"\n  Analyzing structure with LLM...")
    structure = analyze_structure_with_llm(wm, tree_output)
    if structure:
        for key, paths in structure.items():
            if paths:
                print(f"    {key}: {len(paths)} path(s) discovered")
    else:
        print("    Structure analysis failed — using hardcoded fallback paths")

    # 4. Read WM config files (LLM-discovered + hardcoded fallbacks)
    llm_config_paths = [Path(p) for p in structure.get("config_files", [])]
    print(f"\n  Reading {wm} config files...")
    configs = read_wm_configs(wm, llm_config_paths)
    if configs:
        for path in configs:
            print(f"    {path}")
    else:
        print(f"    No config files found for {wm}")

    # 5. LLM extraction from config + discovered scripts
    llm_script_dirs = [Path(p) for p in structure.get("script_dirs", [])]
    scripts_content = collect_script_contents(llm_script_dirs)

    llm_map: dict[str, str] = {}
    if configs or scripts_content:
        print(f"\n  Extracting actions via LLM (config + script contents)...")
        llm_map = extract_actions_with_llm(wm, configs, scripts_content)
        if llm_map:
            for intent, meta in llm_map.items():
                cmd_preview = meta.get("command", "")[:70]
                print(f"    {intent:<22} <- {cmd_preview}")
        else:
            print("    Nothing extracted")

    # 6. Script scan by filename stem (LLM-discovered dirs + hardcoded fallbacks)
    print(f"\n  Scanning script directories...")
    script_map = scan_scripts(llm_script_dirs)
    all_script_dirs = llm_script_dirs + [d for d in SCAN_DIRS if d not in llm_script_dirs]
    for directory in all_script_dirs:
        if directory.is_dir():
            print(f"    {directory}  [found]")
    if script_map:
        for intent, path in script_map.items():
            print(f"    {intent:<22} <- {path}")

    # 7. Scan wallpapers (LLM-discovered dirs + hardcoded fallbacks)
    llm_wallpaper_dirs = [Path(p) for p in structure.get("wallpaper_dirs", [])]
    print(f"\n  Scanning for wallpaper images...")
    wallpapers = scan_wallpapers(llm_wallpaper_dirs)
    if wallpapers:
        print(f"    Found {len(wallpapers)} image(s):")
        for w in wallpapers[:20]:  # show first 20
            print(f"    {w}")
        if len(wallpapers) > 20:
            print(f"    ... and {len(wallpapers) - 20} more")
    else:
        print("    No images found")

    # 8. Build final action list (script > llm > skip)
    actions = build_actions(wm, script_map, llm_map)

    print(f"\n  Final actions ({len(actions)}):")
    if not actions:
        print("    None — add scripts to ~/.local/bin or ~/.config/nixi/scripts/")
    else:
        for a in actions:
            print(f"    {a.intent:<22} [{a.source:<9}]  {a.origin}")

    # 9. Write nixi.toml [actions] block
    write_actions_to_config(config_path, actions)
    print(f"\n  Actions written  : {config_path}")

    # 10. Write nixi-profile.toml (includes wallpaper file list + discovered path context)
    llm_media_roots = structure.get("media_roots", [])
    profile_path = config_path.parent / "nixi-profile.toml"
    write_profile(
        profile_path,
        profile,
        configs,
        wallpapers,
        wallpaper_dirs=structure.get("wallpaper_dirs", []),
        media_roots=llm_media_roots,
    )
    print(f"  Profile written  : {profile_path}")

    print("\nDone. Restart nixi-server to load the new config.\n")
