param([string]$JobFile)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
Add-Type -ReferencedAssemblies System.Speech -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Speech.Synthesis;
using System.Speech.AudioFormat;

public class SapiJob {
    static void PickVoice(SpeechSynthesizer s, string voice) {
        if (!String.IsNullOrEmpty(voice)) { s.SelectVoice(voice); return; }
        string fallback = null;
        foreach (var v in s.GetInstalledVoices()) {
            if (!v.Enabled) continue;
            var info = v.VoiceInfo;
            if (info.Culture.Name.StartsWith("en")) {
                if (info.Gender == VoiceGender.Male) { s.SelectVoice(info.Name); return; }
                if (fallback == null) fallback = info.Name;
            }
        }
        if (fallback != null) s.SelectVoice(fallback);
    }

    public static string Run(string text, string wav, string voice, int rate) {
        var marks = new List<string>();
        using (var s = new SpeechSynthesizer()) {
            PickVoice(s, voice);
            s.Rate = rate;
            s.SetOutputToWaveFile(wav, new SpeechAudioFormatInfo(44100, AudioBitsPerSample.Sixteen, AudioChannel.Mono));
            s.SpeakProgress += (o, e) => {
                lock (marks) {
                    marks.Add(String.Format(CultureInfo.InvariantCulture, "{{\"pos\":{0},\"len\":{1},\"t\":{2}}}",
                        e.CharacterPosition, e.CharacterCount, e.AudioPosition.TotalSeconds));
                }
            };
            s.Speak(text);
            System.Threading.Thread.Sleep(100);
            s.SetOutputToNull();
        }
        lock (marks) {
            return "{\"marks\":[" + String.Join(",", marks) + "]}";
        }
    }

    public static string Voices() {
        var names = new List<string>();
        using (var s = new SpeechSynthesizer()) {
            foreach (var v in s.GetInstalledVoices()) {
                names.Add("\"" + v.VoiceInfo.Name + " | " + v.VoiceInfo.Culture + " | " + v.VoiceInfo.Gender + "\"");
            }
        }
        return "[" + String.Join(",", names) + "]";
    }
}
"@

$job = Get-Content -Raw -Encoding UTF8 $JobFile | ConvertFrom-Json
if ($job.list) {
    [SapiJob]::Voices() | Set-Content -Encoding UTF8 $job.out
    exit 0
}
$results = @()
foreach ($item in $job.items) {
    $results += [SapiJob]::Run($item.text, $item.wav, $job.voice, [int]$job.rate)
}
"[" + ($results -join ",") + "]" | Set-Content -Encoding UTF8 $job.out
