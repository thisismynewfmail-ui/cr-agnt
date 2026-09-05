from pathlib import Path


def test_windows_native_install_path_docs_match_installer() -> None:
    doc = Path("website/docs/user-guide/windows-native.md").read_text()
    install = Path("scripts/install.ps1").read_text()

    # The launchers live in the managed binary dir OUTSIDE the git checkout
    # (CURIE_HOME\bin, next to the managed uv) — NOT the whole venv\Scripts
    # (which would shadow the user's python, #83797) and NOT a dir inside
    # the checkout (which `curie update`'s autostash swept off disk).
    assert "%LOCALAPPDATA%\\curie\\bin" in doc
    assert (
        "Get-Command curie        # should print "
        "C:\\Users\\<you>\\AppData\\Local\\curie\\bin\\curie.exe"
    ) in doc
    # Installer exposes $CurieHome\bin, and must copy the launchers into it.
    assert '$curieBin = "$CurieHome\\bin"' in install
    assert "curie.exe" in install and "curie-acp.exe" in install
    # Guard against regressions to either legacy layout.
    assert '$curieBin = "$InstallDir\\venv\\Scripts"' not in install
    assert '$curieBin = "$InstallDir\\bin"' not in install
