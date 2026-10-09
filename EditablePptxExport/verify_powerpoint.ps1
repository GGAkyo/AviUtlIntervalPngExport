param(
    [Parameter(Mandatory=$true)][string]$InputPptx,
    [Parameter(Mandatory=$true)][string]$OutputFolder
)
$ErrorActionPreference = "Stop"
$inputPath = (Resolve-Path -LiteralPath $InputPptx).Path
if (Test-Path -LiteralPath $OutputFolder) { throw "Output folder already exists." }
$outputPath = [IO.Path]::GetFullPath($OutputFolder)
[IO.Directory]::CreateDirectory($outputPath) | Out-Null
$app = $null
$deck = $null
$created = $false
try {
    try { $app = [Runtime.InteropServices.Marshal]::GetActiveObject("PowerPoint.Application") }
    catch {
        $app = New-Object -ComObject PowerPoint.Application
        $created = $true
    }
    $deck = $app.Presentations.Open($inputPath, -1, 0, 0)
    $results = @()
    foreach ($slide in $deck.Slides) {
        $slide.Export((Join-Path $outputPath ("slide-{0}.png" -f $slide.SlideIndex)), "PNG", 1280, 720)
        $text = @()
        foreach ($shape in $slide.Shapes) {
            if ($shape.HasTextFrame -eq -1 -and $shape.TextFrame.HasText -eq -1) {
                $text += $shape.TextFrame.TextRange.Text
            }
        }
        $results += @{ slide=$slide.SlideIndex; shapes=$slide.Shapes.Count; text=$text }
    }
    $results | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $outputPath "powerpoint-check.json") -Encoding UTF8
    Write-Output ("PowerPoint opened and rendered {0} slides." -f $deck.Slides.Count)
}
finally {
    if ($null -ne $deck) { $deck.Close(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($deck) }
    if ($null -ne $app) {
        if ($created -and $app.Presentations.Count -eq 0) { $app.Quit() }
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app)
    }
}
