<#
=====================================================================
  lingxi 一键上传 + 触发打包脚本（GitHub API 版，不需要 git）
=====================================================================
  用法（PowerShell 里直接运行，无需改任何参数）：
    powershell -ExecutionPolicy Bypass -File "F:\me\husband\lingxi0.3_app\upload_lingxi03.ps1"

  运行后脚本会：
    1. 提示你粘贴 GitHub Token（ghp_ 开头，只需建一次）
    2. 自动上传本文件夹（F:\me\husband\lingxi0.3_app）里的
       main.py / requirements.txt / README.md / .github/workflows/build-apk.yml
       到仓库 XueyingLi1201/lingxi0.3_app（覆盖旧文件）
    3. 自动触发 GitHub Actions 打包 APK，并打印构建地址

  可选参数（一般用不到）：
    -Token ghp_xxx        直接传入 Token，免去提示
    -Repo lingxi0.2_app   换仓库（配合 -Workflow real-build.yml 可上传 0.2）
    -SourceDir 路径       换源目录
    -Workflow 文件名      要触发的工作流（0.2 用 real-build.yml）
    -DryRun               只预览要做什么，不调用 GitHub

  前提：
    1. 仓库 Settings -> Secrets and variables -> Actions 里已配置
       DEEPSEEK_API_KEY、TTS_API_KEY（0.3 可加 ASR_API_KEY）；
    2. 本机能访问 api.github.com（正常网络即可）。

  Token 获取（一次）：
    GitHub 网页 -> 头像 -> Settings -> Developer settings ->
    Personal access tokens -> Tokens (classic) -> Generate new token
    -> 勾选 repo -> Generate token -> 复制 ghp_ 开头的字符串。
=====================================================================
#>
param(
    [string]$Token = "",
    [string]$Owner = "XueyingLi1201",
    [string]$Repo = "lingxi0.3_app",
    [string]$SourceDir = "$PSScriptRoot",
    [string]$Workflow = "build-apk.yml",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Api = "https://api.github.com"

# 没传 Token 时，交互式提示输入
if ([string]::IsNullOrWhiteSpace($Token)) {
    if ($DryRun) { $Token = "dry-run-token" }
    else { $Token = Read-Host -Prompt "请输入 GitHub Token（ghp_ 开头）" }
}
if ([string]::IsNullOrWhiteSpace($Token)) {
    Write-Host "未提供 Token，退出。"
    exit 1
}

$Headers = @{ "Accept" = "application/vnd.github+json"; "X-GitHub-Api-Version" = "2022-11-28" }
$Headers["Authorization"] = "Bearer $Token"

# 需要上传的文件（相对仓库根目录）
$Files = @(
    "main.py",
    "requirements.txt",
    "README.md",
    ".github/workflows/$Workflow"
)

function Invoke-GH {
    param([string]$Method, [string]$Url, $Body = $null)
    $params = @{ Uri = $Url; Method = $Method; Headers = $Headers; ErrorAction = "SilentlyContinue" }
    if ($null -ne $Body) {
        $params.Body = ($Body | ConvertTo-Json -Depth 6)
        $params.ContentType = "application/json"
    }
    try {
        return Invoke-RestMethod @params
    } catch {
        return $null
    }
}

Write-Host "======================================================"
Write-Host "  上传 $Owner/$Repo 并触发打包（工作流: $Workflow）"
Write-Host "  源目录: $SourceDir"
Write-Host "======================================================"

# 0) DryRun：只展示计划，不调用 API
if ($DryRun) {
    Write-Host "[DRY-RUN] 计划上传以下文件："
    foreach ($rel in $Files) {
        $local = Join-Path $SourceDir ($rel -replace "/", "\")
        $exists = Test-Path -LiteralPath $local
        Write-Host ("  - {0}  [本地存在: {1}]" -f $rel, $exists)
    }
    Write-Host "[DRY-RUN] 之后会：检测默认分支 -> 上传/覆盖上述文件 -> 触发 $Workflow"
    exit 0
}

# 1) 检测默认分支
Write-Host "[1/4] 检测仓库默认分支..."
$repoInfo = Invoke-GH GET "$Api/repos/$Owner/$Repo"
if (-not $repoInfo) {
    Write-Host "错误：无法访问仓库 $Owner/$Repo 。"
    Write-Host "  检查：Owner/Repo 拼写、Token 是否有效、Token 是否有 repo 权限。"
    exit 1
}
$Branch = $repoInfo.default_branch
Write-Host "      默认分支: $Branch"

# 2) 上传 / 覆盖文件
Write-Host "[2/4] 上传文件..."
foreach ($rel in $Files) {
    $local = Join-Path $SourceDir ($rel -replace "/", "\")
    if (-not (Test-Path -LiteralPath $local)) {
        Write-Host "      跳过（本地不存在）: $rel"
        continue
    }
    $bytes = [System.IO.File]::ReadAllBytes($local)
    $b64 = [Convert]::ToBase64String($bytes)

    # 文件已存在时需要携带当前 sha
    $sha = $null
    $existing = Invoke-GH GET "$Api/repos/$Owner/$Repo/contents/$rel"
    if ($existing) { $sha = $existing.sha }

    $body = @{ message = "Update $rel (lingxi auto)"; content = $b64 }
    if ($sha) { $body.sha = $sha }

    $res = Invoke-GH PUT "$Api/repos/$Owner/$Repo/contents/$rel" $body
    if ($res) {
        Write-Host ("      成功: {0}（{1} 字节）" -f $rel, $bytes.Length)
    } else {
        Write-Host "      失败: $rel  （请把完整报错发给我）"
    }
}

# 3) 等待工作流注册（刚上传的工作流需要几秒才可触发）
Write-Host "[3/4] 等待工作流注册..."
$wfId = $null
for ($i = 0; $i -lt 30; $i++) {
    $wf = Invoke-GH GET "$Api/repos/$Owner/$Repo/actions/workflows/$Workflow"
    if ($wf) { $wfId = $wf.id; break }
    Start-Sleep -Seconds 2
}
if (-not $wfId) {
    Write-Host "错误：未找到工作流 $Workflow 。请到仓库 Actions 页面确认文件存在；或稍后手动点 Run workflow。"
    exit 1
}
Write-Host "      工作流已就绪 (id=$wfId)"

# 4) 触发构建
Write-Host "[4/4] 触发构建 ($Branch)..."
$dispatch = @{ ref = $Branch }
Invoke-GH POST "$Api/repos/$Owner/$Repo/actions/workflows/$wfId/dispatches" $dispatch

# 5) 取最新一次运行地址
Start-Sleep -Seconds 8
$run = $null
for ($i = 0; $i -lt 20; $i++) {
    $runs = Invoke-GH GET "$Api/repos/$Owner/$Repo/actions/runs?event=workflow_dispatch&per_page=1"
    if ($runs -and $runs.workflow_runs.Count -gt 0) { $run = $runs.workflow_runs[0]; break }
    Start-Sleep -Seconds 3
}
if ($run) {
    Write-Host "构建已开始，地址：$($run.html_url)"
} else {
    Write-Host "已发出触发请求，请到仓库 Actions 页面查看进度。"
}
Write-Host "完成！构建成功后：Actions 页面 -> 点击本次运行 -> 底部 Artifacts 下载 APK。"
