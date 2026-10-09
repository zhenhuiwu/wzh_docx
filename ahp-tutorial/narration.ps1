Add-Type -AssemblyName System.Speech
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$synth.SelectVoice('Microsoft Huihui Desktop')
$synth.Rate = 2
$phrases = @(
  '层次分析法，先分目标、准则和方案。',
  '两两比较，判断相对重要性。',
  '求出权重，再做一致性检验。',
  '按权重汇总，得到方案排序。'
)
try {
  for ($i = 0; $i -lt $phrases.Count; $i++) {
    $synth.SetOutputToWaveFile((Join-Path $PSScriptRoot "voice$($i + 1)-raw.wav"))
    $synth.Speak($phrases[$i])
    $synth.SetOutputToNull()
  }
} finally { $synth.Dispose() }
