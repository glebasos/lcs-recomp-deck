# Linux x86_64 build from Windows, without WSL. Needs LLVM (clang, lld), CMake, Ninja and Python.
# Output: out/lcs-linux-cross/lcs/LCSNative, staged with LCSNative.ini and play.sh in out/lcs-linux-deck/.
param(
    [int]$Jobs = 0,
    [string]$Llvm = "C:\Program Files\LLVM\bin"
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$build = Join-Path $repo "out\lcs-linux-cross"
$stage = Join-Path $repo "out\lcs-linux-deck"

if (-not (Test-Path "$Llvm\clang++.exe") -or -not (Test-Path "$Llvm\ld.lld.exe")) {
    Write-Error "clang++ and ld.lld not found in $Llvm. Install LLVM or pass -Llvm."
}
python -I "$repo\lcs\scripts\fetch_linux_sysroot.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not (Test-Path "$build\build.ninja")) {
    cmake -S $repo -B $build -G Ninja -DCMAKE_BUILD_TYPE=Release `
        "-DCMAKE_TOOLCHAIN_FILE=$repo\lcs\scripts\linux-cross.cmake" `
        "-DLLVM_BIN=$($Llvm -replace '\\','/')" -DPSPRECOMP_BUILD_TESTS=OFF
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
# The generated units take several GB each at -O2; cap the jobs to fit memory.
if ($Jobs -le 0) {
    $gb = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB
    $Jobs = [Math]::Max(1, [Math]::Min([Environment]::ProcessorCount, [int]($gb / 3)))
}
ninja -C $build -j $Jobs LCSNative
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Same layout as the Windows release: LCSNative.ini and game/ next to the binary.
New-Item -ItemType Directory -Force $stage | Out-Null
Copy-Item "$build\lcs\LCSNative" "$stage\LCSNative" -Force
if (-not (Test-Path "$stage\LCSNative.ini")) {  # keep the user's edits on rebuild
    Copy-Item "$repo\lcs\config\LCSNative.ini" "$stage\LCSNative.ini"
}
# SteamOS has no FFmpeg 7; ship the fetched build in lib/, which play.sh puts on the loader path.
New-Item -ItemType Directory -Force "$stage\lib", "$stage\licenses" | Out-Null
Copy-Item "$repo\out\linux-cross\deck-lib\*.so.*" "$stage\lib" -Force
Copy-Item "$repo\out\linux-cross\deck-lib\FFmpeg-LICENSE.txt" "$stage\licenses" -Force
# Button prompt packs made by tools/prompt_pack.py; Display.ButtonPrompts picks one.
foreach ($style in "xbox", "kb") {
    if (Test-Path "$repo\lcs\config\textures\$style") {
        New-Item -ItemType Directory -Force "$stage\textures\$style" | Out-Null
        Copy-Item "$repo\lcs\config\textures\$style\*.rgba" "$stage\textures\$style" -Force
    }
}
$play = @'
#!/usr/bin/env bash
# Runs LCSNative with game/ and LCSNative.ini next to this script.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
if [[ ! -f "$here/game/EBOOT.ELF" ]]; then
    echo "Put EBOOT.ELF and PSP_GAME in $here/game"
    exit 4
fi
chmod +x "$here/LCSNative"
cd "$here"
export LD_LIBRARY_PATH="$here/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$here/LCSNative" --game "$here/game" "$@"
'@
[IO.File]::WriteAllText("$stage\play.sh", $play.Replace("`r`n", "`n"))
Write-Host "Staged $stage"
