param([string]$Manifest, [string]$Destination)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$d = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
$bitmap = [System.Drawing.Bitmap]::new([int]$d.width, [int]$d.height)
$g = [System.Drawing.Graphics]::FromImage($bitmap)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$issues = @()
foreach ($c in $d.commands) {
    if ($c.op -eq 'box') {
        $p = [System.Drawing.Drawing2D.GraphicsPath]::new()
        $r = [single]$c.radius * 2
        if ($r -eq 0) { $p.AddRectangle([System.Drawing.RectangleF]::new($c.x,$c.y,$c.w,$c.h)) }
        else {
            $p.AddArc($c.x,$c.y,$r,$r,180,90)
            $p.AddArc(($c.x+$c.w-$r),$c.y,$r,$r,270,90)
            $p.AddArc(($c.x+$c.w-$r),($c.y+$c.h-$r),$r,$r,0,90)
            $p.AddArc($c.x,($c.y+$c.h-$r),$r,$r,90,90)
            $p.CloseFigure()
        }
        $b = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($c.fill))
        $pen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml($c.stroke),2)
        if ($c.dash) { $pen.DashStyle = [System.Drawing.Drawing2D.DashStyle]::Dash }
        $g.FillPath($b,$p); $g.DrawPath($pen,$p)
        $p.Dispose(); $pen.Dispose(); $b.Dispose()
    } elseif ($c.op -eq 'text') {
        $style = [System.Drawing.FontStyle]::Regular
        if ($c.bold) { $style = [System.Drawing.FontStyle]::Bold }
        $font = [System.Drawing.Font]::new('Segoe UI',[single]$c.size,$style,[System.Drawing.GraphicsUnit]::Pixel)
        $b = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($c.color))
        $extent = $g.MeasureString([string]$c.value,$font)
        if ($extent.Width -gt $c.width) { $issues += "$($c.value): $([int]$extent.Width) > $($c.width)" }
        $g.DrawString([string]$c.value,$font,$b,[single]$c.x,[single]$c.y)
        $b.Dispose(); $font.Dispose()
    } else {
        $pen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml($c.color),[single]$c.width)
        if ($c.dash) { $pen.DashStyle = [System.Drawing.Drawing2D.DashStyle]::Dash }
        $points = [System.Drawing.PointF[]]@($c.points | ForEach-Object { [System.Drawing.PointF]::new([single]$_[0],[single]$_[1]) })
        $g.DrawLines($pen,$points)
        $last=$points[-1];$prev=$points[-2];$dx=$last.X-$prev.X;$dy=$last.Y-$prev.Y
        $length=[Math]::Sqrt($dx*$dx+$dy*$dy);$ux=$dx/$length;$uy=$dy/$length
        $head=[System.Drawing.PointF[]]@($last,
            [System.Drawing.PointF]::new(($last.X-$ux*14-$uy*7),($last.Y-$uy*14+$ux*7)),
            [System.Drawing.PointF]::new(($last.X-$ux*14+$uy*7),($last.Y-$uy*14-$ux*7)))
        $b=[System.Drawing.SolidBrush]::new($pen.Color);$g.FillPolygon($b,$head)
        $b.Dispose();$pen.Dispose()
    }
}
$bitmap.Save($Destination,[System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose();$bitmap.Dispose()
if ($issues.Count) { Write-Output ($issues -join "`n"); exit 1 }
Write-Output "DIAGRAM_RENDER_OK: $Destination"
