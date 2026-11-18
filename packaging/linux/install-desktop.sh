#!/usr/bin/env bash
# Installs RZGit's desktop entry and icons for the current user, so app launchers
# and menus list it with its icon. Run it again to reinstall (for example after moving
# the checkout or changing the icons); --uninstall removes everything it installed.
#
#   packaging/linux/install-desktop.sh [--uninstall]
#
# Files go under $XDG_DATA_HOME (default ~/.local/share):
#   applications/rzgit.desktop             this folder's template, Exec filled in
#   icons/hicolor/<N>x<N>/apps/rzgit.png   each icon PNG of the app, as it is
#   applications/.rzgit-installed          the list of files above, for the next run
#
# An install made when the app was called RZGitClient (app id rzgitclient) is removed too.
#
# Environment:
#   RZGIT_EXEC              the program the entry starts (default: this checkout's .venv/bin/rzgit)
#   RZGIT_NO_CACHE_REFRESH  set to skip refreshing the desktop's caches (the tests set it)
set -euo pipefail

app_id=rzgit
old_app_id=rzgitclient  # the app's id before RZG-59
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
icons="$root/src/rzgit/ui/assets/icon"
entry="$data/applications/$app_id.desktop"

# Removes the files the last install of the app id $1 listed, one named file at a time.
# Only paths under $data that end in that id's entry or icon name are touched.
remove_listed() {
    local id="$1"
    local manifest="$data/applications/.$id-installed"
    [[ -f "$manifest" ]] || return 0
    local path
    while IFS= read -r path; do
        case "$path" in
            "$data"/applications/"$id".desktop | "$data"/icons/hicolor/*/apps/"$id".png)
                rm -f -- "$path" ;;
        esac
    done < "$manifest"
    rm -f -- "$manifest"
}

remove_installed() {
    remove_listed "$app_id"
    remove_listed "$old_app_id"
}

refresh_caches() {
    [[ -n "${RZGIT_NO_CACHE_REFRESH:-}" ]] && return 0
    if command -v update-desktop-database > /dev/null; then
        update-desktop-database -q "$data/applications" || true
    fi
    if command -v gtk-update-icon-cache > /dev/null && [[ -f "$data/icons/hicolor/index.theme" ]]; then
        gtk-update-icon-cache -q -t -f "$data/icons/hicolor" || true
    fi
    if command -v kbuildsycoca6 > /dev/null; then
        kbuildsycoca6 > /dev/null 2>&1 || true
    fi
}

install_all() {
    local exec_path="${RZGIT_EXEC:-$root/.venv/bin/rzgit}"
    if [[ ! -x "$exec_path" ]]; then
        echo "install-desktop: $exec_path isn't an executable. Set up the venv first (see README.md)," >&2
        echo "or point RZGIT_EXEC at the rzgit to start." >&2
        exit 1
    fi
    # Exec's quoting rules: a path with any of these can't be written in plainly.
    if [[ "$exec_path" == *[\"\`\$\\]* ]]; then
        echo "install-desktop: can't write $exec_path into the entry (it has \" \` \$ or \\)." >&2
        exit 1
    fi

    remove_installed
    mkdir -p "$data/applications"
    local listed=()

    local template
    template="$(< "$here/$app_id.desktop")"
    printf '%s\n' "${template//@EXEC@/$exec_path}" > "$entry"
    listed+=("$entry")

    local png name size target
    for png in "$icons"/rzgit-*.png; do
        name="$(basename "$png")"
        [[ "$name" =~ ^rzgit-([0-9]+)\.png$ ]] || continue
        size="${BASH_REMATCH[1]}"
        target="$data/icons/hicolor/${size}x${size}/apps/$app_id.png"
        mkdir -p "$(dirname "$target")"
        cp -f -- "$png" "$target"
        listed+=("$target")
    done

    printf '%s\n' "${listed[@]}" > "$data/applications/.$app_id-installed"
    refresh_caches
    echo "Installed $entry and ${#listed[@]} files in all. Exec: $exec_path"
}

case "${1:-}" in
    "") install_all ;;
    --uninstall)
        remove_installed
        refresh_caches
        echo "Removed RZGit's desktop entry and icons from $data." ;;
    *)
        echo "usage: $0 [--uninstall]" >&2
        exit 2 ;;
esac
