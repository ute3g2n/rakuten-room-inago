param(
  [string]$Root = (Join-Path (Get-Location) "rakuten-room"),
  [string]$SkillRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"

function Read-JsonObject {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return $null }
  $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
  if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
  return $raw | ConvertFrom-Json
}

function Write-JsonFile {
  param([string]$Path, [object]$Value)
  $json = $Value | ConvertTo-Json -Depth 12
  $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
  [System.IO.File]::WriteAllText($Path, $json, $utf8NoBom)
}

function Normalize-Cell {
  param([string]$Value)
  if ($null -eq $Value) { return $null }
  $text = ($Value -replace "<br\s*/?>", " " -replace "\\\|", "&#124;").Trim()
  if ($text -eq "" -or $text -eq "-" -or $text -eq "—" -or $text -eq "なし") { return $null }
  return $text
}

function Normalize-Date {
  param([string]$Value)
  if ($Value -match "\d{4}-\d{2}-\d{2}") { return $Matches[0] }
  return $null
}

function Normalize-Url {
  param([string]$Value)
  $url = Normalize-Cell $Value
  if ($null -eq $url) { return $null }
  if ($url -match "\((https?://[^)]+)\)") { $url = $Matches[1] }
  if ($url -match "https?://\S+") { $url = $Matches[0] }
  return $url.Trim()
}

function Split-MarkdownRow {
  param([string]$Line)
  $trimmed = $Line.Trim()
  if (-not $trimmed.StartsWith("|")) { return @() }
  $trimmed = $trimmed.Trim("|")
  return @($trimmed -split "\|" | ForEach-Object { Normalize-Cell $_ })
}

function Read-MarkdownTable {
  param([string]$Path, [string]$HeaderPattern)
  if (-not (Test-Path -LiteralPath $Path)) { return @() }
  $lines = Get-Content -LiteralPath $Path -Encoding UTF8
  $headerIndex = -1
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match $HeaderPattern) {
      $headerIndex = $i
      break
    }
  }
  if ($headerIndex -lt 0) { return @() }

  $headers = Split-MarkdownRow $lines[$headerIndex]
  $rows = @()
  for ($i = $headerIndex + 2; $i -lt $lines.Count; $i++) {
    $line = $lines[$i]
    if (-not $line.Trim().StartsWith("|")) { break }
    $cells = Split-MarkdownRow $line
    if ($cells.Count -lt 2) { continue }
    $row = [ordered]@{}
    for ($c = 0; $c -lt $headers.Count; $c++) {
      $row[$headers[$c]] = if ($c -lt $cells.Count) { $cells[$c] } else { $null }
    }
    $rows += [pscustomobject]$row
  }
  return $rows
}

function Get-CacheImage {
  param([object]$Cache, [string]$Url)
  if ($null -eq $Cache -or [string]::IsNullOrWhiteSpace($Url)) { return $null }
  $candidates = @($Url)
  if ($Url.EndsWith("/")) { $candidates += $Url.TrimEnd("/") } else { $candidates += "$Url/" }
  foreach ($candidate in $candidates) {
    $prop = $Cache.PSObject.Properties[$candidate]
    if ($prop -and -not [string]::IsNullOrWhiteSpace([string]$prop.Value)) {
      return [string]$prop.Value
    }
  }
  return $null
}

function Set-CacheImage {
  param([object]$Cache, [string]$Url, [string]$ImageUrl)
  if ([string]::IsNullOrWhiteSpace($Url) -or [string]::IsNullOrWhiteSpace($ImageUrl)) { return }
  if ($null -eq $Cache) { $script:imageCache = [pscustomobject]@{}; $Cache = $script:imageCache }
  $prop = $Cache.PSObject.Properties[$Url]
  if ($prop) { $prop.Value = $ImageUrl } else { $Cache | Add-Member -NotePropertyName $Url -NotePropertyValue $ImageUrl }
}

function Get-OgImage {
  param([string]$Url)
  if ([string]::IsNullOrWhiteSpace($Url) -or -not $Url.StartsWith("http")) { return $null }
  try {
    $html = & curl.exe -sL $Url -H "User-Agent: Mozilla/5.0"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($html)) { return $null }
    $metaTags = [regex]::Matches($html, "<meta\b[^>]*>", "IgnoreCase")
    foreach ($tag in $metaTags) {
      if ($tag.Value -notmatch "og:image") { continue }
      if ($tag.Value -match "content\s*=\s*['""']([^'""']+)['""']") { return $Matches[1] }
    }
  } catch {
    return $null
  }
  return $null
}

function Get-ProductImage {
  param([object]$Cache, [string]$Url)
  $image = Get-CacheImage -Cache $Cache -Url $Url
  if ($image) { return $image }
  $image = Get-OgImage -Url $Url
  if ($image) {
    Set-CacheImage -Cache $Cache -Url $Url -ImageUrl $image
    return $image
  }
  return $null
}

function Find-ProductForPost {
  param([array]$Products, [string]$PostProductName)
  if ([string]::IsNullOrWhiteSpace($PostProductName)) { return $null }
  $exact = $Products | Where-Object { $_.product_name -eq $PostProductName } | Select-Object -First 1
  if ($exact) { return $exact }
  return $Products | Where-Object {
    $PostProductName.Contains($_.product_name) -or $_.product_name.Contains($PostProductName)
  } | Select-Object -First 1
}

function Get-DraftText {
  param([string]$Root, [string]$Date, [string]$ProductName, [string]$Fallback)
  $datedDraft = Join-Path $Root "drafts\$Date.md"
  if (Test-Path -LiteralPath $datedDraft) {
    $text = Get-Content -LiteralPath $datedDraft -Raw -Encoding UTF8
    if (-not [string]::IsNullOrWhiteSpace($text)) { return $text.Trim() }
  }

  $postDrafts = Join-Path $Root "post-drafts.md"
  if (Test-Path -LiteralPath $postDrafts) {
    $text = Get-Content -LiteralPath $postDrafts -Raw -Encoding UTF8
    $escapedName = [regex]::Escape($ProductName)
    $match = [regex]::Match($text, "(?ms)^##\s+$escapedName\s*\r?\n(?<body>.*?)(?=^##\s+|\z)")
    if ($match.Success) { return $match.Groups["body"].Value.Trim() }
  }
  return $Fallback
}

$calendarPath = Join-Path $Root "calendar.json"
if (-not (Test-Path -LiteralPath $calendarPath)) {
  Write-Output "calendar.json が無いため、カレンダー同期はスキップしました。"
  exit 0
}

$config = Read-JsonObject $calendarPath
$supabaseUrl = [string]$config.SUPABASE_URL
$publishableKey = [string]$config.PUBLISHABLE_KEY
$secretKey = [string]$config.SECRET_KEY
if ([string]::IsNullOrWhiteSpace($supabaseUrl) -or [string]::IsNullOrWhiteSpace($secretKey)) {
  throw "calendar.json に SUPABASE_URL または SECRET_KEY がありません。"
}

$cachePath = Join-Path $Root ".image-cache.json"
$script:imageCache = Read-JsonObject $cachePath
if ($null -eq $script:imageCache) { $script:imageCache = [pscustomobject]@{} }

$productsPath = Join-Path $Root "products.md"
$productRows = Read-MarkdownTable -Path $productsPath -HeaderPattern "^\|\s*優先度\s*\|\s*商品名\s*\|\s*URL\s*\|"
$products = @()
foreach ($row in $productRows) {
  $url = Normalize-Url $row.URL
  if (-not $url) { continue }
  $imageUrl = Get-ProductImage -Cache $script:imageCache -Url $url
  $products += [pscustomobject]@{
    product_name = $row.商品名
    product_url = $url
    priority = $row.優先度
    price = $row.価格
    pain_point = $row.刺さる悩み
    role_fit = $row.確定役割
    heat_evidence = $row."加熱根拠（＋クーポン期限）"
    checked_on = (Normalize-Date $row.確認日)
    last_posted = (Normalize-Date $row.最終投稿日)
    listing = "候補"
    exclude_reason = $null
    image_url = $imageUrl
  }
}

$postsPath = Join-Path $Root "posts-log.md"
$postRows = Read-MarkdownTable -Path $postsPath -HeaderPattern "^\|\s*日付\s*\|\s*商品\s*\|\s*役割\s*\|"
$posts = @()
foreach ($row in $postRows) {
  $postDate = Normalize-Date $row.日付
  if (-not $postDate) { continue }
  $product = Find-ProductForPost -Products $products -PostProductName $row.商品
  $productUrl = if ($product) { $product.product_url } else { $null }
  $imageUrl = if ($product) { $product.image_url } else { $null }
  $hookType = $null
  if ($row."投稿文の要旨（冒頭型）" -match "（([^）]+型)）") { $hookType = $Matches[1] }
  $posts += [pscustomobject]@{
    post_date = $postDate
    post_time = $row."予定/実施時刻"
    post_role = $row.役割
    product_name = $row.商品
    product_url = $productUrl
    image_url = $imageUrl
    price = if ($product) { $product.price } else { $null }
    hook_type = $hookType
    post_text = (Get-DraftText -Root $Root -Date $postDate -ProductName $row.商品 -Fallback $row."投稿文の要旨（冒頭型）")
    status = $row.実施
    reaction = $row.反応メモ
    learning = $row.学び
  }
}

if ($products.Count -eq 0) { throw "products.md から候補商品を読み取れませんでした。" }
if ($posts.Count -eq 0) { throw "posts-log.md から投稿計画を読み取れませんでした。" }

$postsJson = Join-Path $Root ".sync-posts.json"
$productsJson = Join-Path $Root ".sync-products.json"
Write-JsonFile -Path $postsJson -Value $posts
Write-JsonFile -Path $productsJson -Value $products
Write-JsonFile -Path $cachePath -Value $script:imageCache

$headers = @(
  "-H", "apikey: $secretKey",
  "-H", "Authorization: Bearer $secretKey",
  "-H", "Content-Type: application/json",
  "-H", "Prefer: resolution=merge-duplicates,return=representation"
)

$postsResponse = Join-Path $Root ".sync-posts.response.json"
$productsResponse = Join-Path $Root ".sync-products.response.json"
$postsStatus = & curl.exe -s -o $postsResponse -w "%{http_code}" -X POST "$supabaseUrl/rest/v1/rakuten_posts?on_conflict=post_date,product_name" @headers --data "@$postsJson"
if ($LASTEXITCODE -ne 0 -or $postsStatus -notin @("200", "201")) {
  $message = if (Test-Path -LiteralPath $postsResponse) { Get-Content -LiteralPath $postsResponse -Raw -Encoding UTF8 } else { "" }
  throw "rakuten_posts の同期に失敗しました。HTTP $postsStatus $message"
}
$productsStatus = & curl.exe -s -o $productsResponse -w "%{http_code}" -X POST "$supabaseUrl/rest/v1/rakuten_products?on_conflict=product_url" @headers --data "@$productsJson"
if ($LASTEXITCODE -ne 0 -or $productsStatus -notin @("200", "201")) {
  $message = if (Test-Path -LiteralPath $productsResponse) { Get-Content -LiteralPath $productsResponse -Raw -Encoding UTF8 } else { "" }
  throw "rakuten_products の同期に失敗しました。HTTP $productsStatus $message"
}

$verifyRaw = & curl.exe -s "$supabaseUrl/rest/v1/rakuten_posts?select=post_date,product_name,image_url&order=post_date" -H "apikey: $secretKey" -H "Authorization: Bearer $secretKey"
if ($LASTEXITCODE -ne 0) { throw "同期後の確認取得に失敗しました。" }
$verified = @($verifyRaw | ConvertFrom-Json)
$missingImages = @($verified | Where-Object {
  $matchingPost = $_
  $sourcePost = $posts | Where-Object { $_.post_date -eq $matchingPost.post_date -and $_.product_name -eq $matchingPost.product_name } | Select-Object -First 1
  $sourcePost -and $sourcePost.image_url -and [string]::IsNullOrWhiteSpace([string]$matchingPost.image_url)
})

$webDir = Join-Path $Root "calendar-web"
if (-not (Test-Path -LiteralPath $webDir)) { New-Item -ItemType Directory -Path $webDir | Out-Null }
$sourceIndex = Join-Path $SkillRoot "webapp\index.html"
if ((Test-Path -LiteralPath $sourceIndex) -and -not (Test-Path -LiteralPath (Join-Path $webDir "index.html"))) {
  Copy-Item -LiteralPath $sourceIndex -Destination (Join-Path $webDir "index.html")
}
if (-not [string]::IsNullOrWhiteSpace($publishableKey)) {
  $configJs = "window.SUPABASE_CONFIG = { URL: `"$supabaseUrl`", ANON_KEY: `"$publishableKey`" };"
  [System.IO.File]::WriteAllText((Join-Path $webDir "config.js"), $configJs, [System.Text.UTF8Encoding]::new($false))
}

Remove-Item -LiteralPath $postsJson, $productsJson, $postsResponse, $productsResponse -ErrorAction SilentlyContinue

if ($missingImages.Count -gt 0) {
  Write-Output "同期は完了しましたが、画像URLが空の投稿が $($missingImages.Count) 件あります。"
} else {
  Write-Output "同期完了: 投稿 $($posts.Count) 件、商品 $($products.Count) 件。画像URLも確認済みです。"
}
