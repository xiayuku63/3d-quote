# push.ps1
# PowerShell 脚本 - 自动提交并推送代码到 GitHub (Windows)
#
# 用法:
#   .\push.ps1
#   .\push.ps1 "feat: 更新切片引擎"

param(
    [string]$Message = ""
)

$Remote = "origin"
$Branch = "main"

Write-Host ""
Write-Host "============================================================"
Write-Host "  Git Auto Push to GitHub"
Write-Host "  Remote: ${Remote}  |  Branch: ${Branch}"
Write-Host "============================================================"
Write-Host ""

Write-Host "[STATUS] Current changes:"
git status --short
Write-Host ""

$changes = git status --porcelain 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Not a git repository. Run this script from the project root." -ForegroundColor Red
    exit 1
}
if (-not $changes) {
    Write-Host "[DONE] No changes to commit." -ForegroundColor Yellow
    exit 0
}

if (-not $Message) {
    $Message = Read-Host "Enter commit message"
    if (-not $Message) {
        Write-Host "[ERROR] Commit message cannot be empty." -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "[STEP] git add -A"
git add -A
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] git add failed." -ForegroundColor Red
    exit 1
}

Write-Host "[STEP] git commit -m `"$Message`""
git commit -m "$Message"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] git commit failed." -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Commit succeeded." -ForegroundColor Green

Write-Host ""
Write-Host "[STEP] git push ${Remote} ${Branch}"
git push $Remote $Branch
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "   PUSH SUCCESS" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "[ERROR] Push failed. Check network or Git credentials." -ForegroundColor Red
    exit 1
}
