using UnityEngine;
using UnityEngine.Video;
using UnityEditor;
using System.Collections.Generic;
using System.IO;

public class GenerateVideoCues
{
    [System.Serializable]
    public class CueEntry
    {
        public string cueName;       // becomes the Looping Effect name Lua fires by
        public string videoClipPath; // path to an already-imported VideoClip asset
        public bool loop;            // loop the clip natively (menus, infinite stills)
    }

    [System.Serializable]
    private class CueManifest
    {
        public List<CueEntry> cues;
    }

    [MenuItem("TTS Modding/Generate Video Cue Objects")]
    public static void Generate()
    {
        GameObject template = Selection.activeGameObject;
        if (template == null)
        {
            Debug.LogError("Select your working template plane first -- the " +
                "one with VideoPlayer (Render Texture, Play On Awake) " +
                "already confirmed working.");
            return;
        }

        var templateVP = template.GetComponent<VideoPlayer>();
        if (templateVP == null)
        {
            Debug.LogError("Selected object needs a VideoPlayer component.");
            return;
        }

        if (template.transform.parent == null)
        {
            Debug.LogError("Template needs a parent object -- that parent is " +
                "where TTSAssetBundleEffects and the generated Looping Effects go.");
            return;
        }

        var root = template.transform.parent.gameObject;
        var effects = root.GetComponent<TTSAssetBundleEffects>();
        if (effects == null)
        {
            Debug.LogError("Template's parent needs a TTSAssetBundleEffects component.");
            return;
        }

        string manifestPath = EditorUtility.OpenFilePanel(
            "Select cue manifest JSON", Application.dataPath, "json");
        if (string.IsNullOrEmpty(manifestPath)) return;

        var manifest = JsonUtility.FromJson<CueManifest>(File.ReadAllText(manifestPath));
        if (manifest?.cues == null || manifest.cues.Count == 0)
        {
            Debug.LogError("Manifest had no cues, or failed to parse.");
            return;
        }

        int generated = 0, skipped = 0, looping = 0;

        foreach (var cue in manifest.cues)
        {
            var videoClip = AssetDatabase.LoadAssetAtPath<VideoClip>(cue.videoClipPath);
            if (videoClip == null)
            {
                Debug.LogWarning($"No VideoClip at '{cue.videoClipPath}' for " +
                    $"cue '{cue.cueName}' -- skipping.");
                skipped++;
                continue;
            }

            // Duplicate the known-working template rather than building a
            // new plane from scratch -- inherits its material/shader/aspect
            // ratio setup without re-deriving any of it.
            var cueObj = Object.Instantiate(template, template.transform.parent);
            cueObj.name = "cue_" + cue.cueName;
            cueObj.SetActive(false);

            var vp = cueObj.GetComponent<VideoPlayer>();
            vp.clip = videoClip;
            vp.playOnAwake = true;
            vp.isLooping = cue.loop;

            effects.LoopingEffects.Add(new TTSAssetBundleEffects.TTSLoopingEffect
            {
                Name = cue.cueName,
                GameObjects = new List<TTSAssetBundleEffects.TTSGameObject>
                {
                    new TTSAssetBundleEffects.TTSGameObject { gameObject = cueObj },
                },
            });

            generated++;
            if (cue.loop) looping++;
        }

        EditorUtility.SetDirty(effects);
        AssetDatabase.SaveAssets();

        Debug.Log($"Generated {generated} cue objects, {looping} looping ({skipped} skipped -- " +
            "missing VideoClip assets). Re-select the root object to see the " +
            "new Looping Effects in the Inspector.");
    }
}
