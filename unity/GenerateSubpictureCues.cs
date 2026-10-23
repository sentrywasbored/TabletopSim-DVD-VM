using UnityEngine;
using UnityEditor;
using System.Collections.Generic;
using System.IO;

// Builds the subpicture overlay bundle: one Looping Effect per pre-rendered
// subpicture image (button highlights, always-visible base layers), each a
// sprite placed exactly where that image sits on the 720x480 DVD frame.
// The host shows one by playing its effect on an overlay object, the same way
// video cues are played -- no image URLs.
//
// Prefab layout (a duplicate of the video prefab, cue objects removed):
//
//   <root>                 TTSAssetBundleEffects + the screen's MeshFilter/MeshRenderer
//                          (the surface VideoOut is drawn on). Its mesh UVs say where
//                          every frame pixel is. If the mesh has several faces (a box),
//                          the largest one facing the root's local -Z is used, and the
//                          choice is logged. Keep the renderer as it is: in TTS the
//                          overlay objects sit exactly on the screen, so this mesh is
//                          drawn twice in the same place -- give the overlay prefab's
//                          renderer a fully transparent material, or disable it.
//     _TEMPLATE SPRITE     an empty GameObject with a SpriteRenderer (sprite left
//                          empty). Its material / sorting settings are what every
//                          generated sprite inherits. Select THIS, then run.
//     _FRAME  (optional)   any child with a MeshFilter to use instead of the root's
//                          mesh, e.g. a Quad aligned by hand.
//     cue_None, cue_SPU.Calibrate, cue_S_<id> ...   (generated)
//
// Manifest: <name>_spu_unity.json from build_spu_manifest.py. Copy the disc's
// SPU folder into the project as Assets/SPU (img/ + calibrate.png) first; the
// sprite import settings are applied here.
public class GenerateSubpictureCues
{
    [System.Serializable]
    public class SpuEntry
    {
        public string cueName;     // Looping Effect name Lua fires by
        public string spritePath;  // Assets/SPU/img/<id>.png ("" = empty effect)
        public int x, y, w, h;     // rectangle on the 720x480 DVD frame, top-left origin
        public int lift;           // depth step in front of the video (base 1, highlight 2)
    }

    [System.Serializable]
    private class SpuManifest
    {
        public int frameWidth = 720;
        public int frameHeight = 480;
        public List<SpuEntry> cues;
    }

    // Each lift step, as a fraction of the frame's width, toward the viewer.
    const float LiftStep = 0.0015f;
    const float PixelsPerUnit = 100f;

    [MenuItem("TTS Modding/Generate Subpicture Cue Objects")]
    public static void Generate()
    {
        GameObject template = Selection.activeGameObject;
        if (template == null || template.GetComponent<SpriteRenderer>() == null)
        {
            Debug.LogError("Select _TEMPLATE SPRITE first -- a GameObject with a SpriteRenderer.");
            return;
        }
        Transform root = template.transform.parent;
        if (root == null)
        {
            Debug.LogError("_TEMPLATE SPRITE needs a parent: the root with TTSAssetBundleEffects.");
            return;
        }
        var effects = root.GetComponent<TTSAssetBundleEffects>();
        if (effects == null)
        {
            Debug.LogError("The template's parent needs a TTSAssetBundleEffects component.");
            return;
        }
        Transform frameOverride = root.Find("_FRAME");
        MeshFilter mf = frameOverride != null ? frameOverride.GetComponent<MeshFilter>() : root.GetComponent<MeshFilter>();
        if (mf == null || mf.sharedMesh == null)
        {
            Debug.LogError("No screen mesh: the root needs the MeshFilter the video is drawn on " +
                "(or add a child _FRAME with a MeshFilter).");
            return;
        }
        Transform frame = mf.transform;
        if (!FrameAxes(mf.sharedMesh, frame, out Vector3 o, out Vector3 u, out Vector3 v, out Vector3 n, out string faceInfo))
        {
            Debug.LogError($"{frame.name}'s mesh has no usable UVs.");
            return;
        }
        Debug.Log($"Frame surface: {frame.name} ({mf.sharedMesh.name}) -- {faceInfo}");
        // frame in world space: pixel (px,py) -> Ow + Uw*px/W + Vw*(1 - py/H)
        Vector3 Ow = frame.TransformPoint(o);
        Vector3 Uw = frame.TransformVector(u);
        Vector3 Vw = frame.TransformVector(v);
        Vector3 Nw = frame.TransformDirection(n).normalized;
        Quaternion facing = Quaternion.LookRotation(Vector3.Cross(Uw, Vw), Vw);

        string manifestPath = EditorUtility.OpenFilePanel(
            "Select subpicture manifest JSON", Application.dataPath, "json");
        if (string.IsNullOrEmpty(manifestPath)) return;
        var manifest = JsonUtility.FromJson<SpuManifest>(File.ReadAllText(manifestPath));
        if (manifest?.cues == null || manifest.cues.Count == 0)
        {
            Debug.LogError("Manifest had no cues, or failed to parse.");
            return;
        }
        float W = manifest.frameWidth, H = manifest.frameHeight;

        // Re-running replaces what an earlier run generated.
        var old = new List<GameObject>();
        foreach (Transform c in root)
            if (c.name.StartsWith("cue_")) old.Add(c.gameObject);
        if (old.Count > 0 || effects.LoopingEffects.Count > 0)
        {
            if (!EditorUtility.DisplayDialog("Replace subpicture cues?",
                $"The root already has {old.Count} cue_ objects and {effects.LoopingEffects.Count} " +
                "Looping Effects. Remove them and generate fresh?", "Replace", "Cancel"))
                return;
            foreach (var g in old) Object.DestroyImmediate(g);
            effects.LoopingEffects.Clear();
        }

        // Sprite import settings, batched.
        AssetDatabase.StartAssetEditing();
        try
        {
            foreach (var cue in manifest.cues)
            {
                if (string.IsNullOrEmpty(cue.spritePath)) continue;
                var ti = AssetImporter.GetAtPath(cue.spritePath) as TextureImporter;
                if (ti == null) continue;
                bool dirty = ti.textureType != TextureImporterType.Sprite || ti.mipmapEnabled ||
                             ti.spritePixelsPerUnit != PixelsPerUnit ||
                             ti.textureCompression != TextureImporterCompression.Uncompressed;
                if (!dirty) continue;
                ti.textureType = TextureImporterType.Sprite;
                ti.spriteImportMode = SpriteImportMode.Single;
                ti.spritePixelsPerUnit = PixelsPerUnit;
                ti.mipmapEnabled = false;
                ti.alphaIsTransparency = true;
                ti.wrapMode = TextureWrapMode.Clamp;
                ti.filterMode = FilterMode.Bilinear;
                ti.textureCompression = TextureImporterCompression.Uncompressed;
                ti.npotScale = TextureImporterNPOTScale.None;
                ti.SaveAndReimport();
            }
        }
        finally
        {
            AssetDatabase.StopAssetEditing();
        }

        // The root is the (usually non-uniformly scaled) screen object, so a sprite's local
        // scale has to undo the root's scale along the sprite's own axes. That's exact when
        // the frame's axes line up with the root's (any 90-degree arrangement).
        float perWorldX = root.InverseTransformVector(Uw.normalized).magnitude;
        float perWorldY = root.InverseTransformVector(Vw.normalized).magnitude;
        float perWorldZ = root.InverseTransformVector(Nw).magnitude;
        Vector3 lu = root.InverseTransformDirection(Uw.normalized), lv = root.InverseTransformDirection(Vw.normalized);
        if (Mathf.Max(Mathf.Abs(lu.x), Mathf.Abs(lu.y), Mathf.Abs(lu.z)) < 0.999f ||
            Mathf.Max(Mathf.Abs(lv.x), Mathf.Abs(lv.y), Mathf.Abs(lv.z)) < 0.999f)
            Debug.LogWarning("The picture's axes aren't aligned with the root's; with a non-uniform " +
                "root scale the sprites will be skewed. Use a _FRAME child instead.");

        int generated = 0, skipped = 0;
        foreach (var cue in manifest.cues)
        {
            GameObject go;
            if (string.IsNullOrEmpty(cue.spritePath))
            {
                go = new GameObject();
                go.transform.SetParent(root, false);
            }
            else
            {
                var sprite = AssetDatabase.LoadAssetAtPath<Sprite>(cue.spritePath);
                if (sprite == null)
                {
                    Debug.LogWarning($"No sprite at '{cue.spritePath}' for '{cue.cueName}' -- skipping.");
                    skipped++;
                    continue;
                }
                go = Object.Instantiate(template, root);
                go.GetComponent<SpriteRenderer>().sprite = sprite;

                // centre of the image's rectangle on the frame, lifted toward the viewer
                float cx = (cue.x + cue.w * 0.5f) / W;
                float cy = 1f - (cue.y + cue.h * 0.5f) / H;
                go.transform.position = Ow + Uw * cx + Vw * cy + Nw * (LiftStep * cue.lift * Uw.magnitude);
                go.transform.rotation = facing;
                // world size wanted: (w/W)|Uw| x (h/H)|Vw|; the sprite's own size is w,h / PixelsPerUnit
                Vector3 native = sprite.bounds.size;
                go.transform.localScale = new Vector3(
                    cue.w / W * Uw.magnitude / native.x * perWorldX,
                    cue.h / H * Vw.magnitude / native.y * perWorldY,
                    perWorldZ);
            }
            go.name = "cue_" + cue.cueName;
            go.SetActive(false);

            effects.LoopingEffects.Add(new TTSAssetBundleEffects.TTSLoopingEffect
            {
                Name = cue.cueName,
                GameObjects = new List<TTSAssetBundleEffects.TTSGameObject>
                {
                    new TTSAssetBundleEffects.TTSGameObject { gameObject = go },
                },
            });
            generated++;
        }

        EditorUtility.SetDirty(effects);
        AssetDatabase.SaveAssets();
        Debug.Log($"Generated {generated} subpicture cues ({skipped} skipped). Effect 0 is None. " +
            "Check alignment: activate cue_SPU.Calibrate -- its red border should trace the screen's edges.");
    }

    // Picks the face the video is seen on and fits it: position = o + u*uv.x + v*uv.y in
    // the mesh's local space, n = that face's front normal. Faces are grouped by vertex
    // normal; the largest by world-space area wins, ties (a thin box's front and back)
    // going to the one facing local -Z, Unity's front.
    static bool FrameAxes(Mesh m, Transform t, out Vector3 o, out Vector3 u, out Vector3 v,
                          out Vector3 n, out string info)
    {
        o = u = v = n = Vector3.zero;
        info = "";
        var pos = m.vertices;
        var uv = m.uv;
        var nor = m.normals;
        var tri = m.triangles;
        if (uv == null || uv.Length != pos.Length || pos.Length < 3) return false;
        bool hasNormals = nor != null && nor.Length == pos.Length;

        // group vertices by (rounded) normal
        var groupOf = new int[pos.Length];
        var keys = new List<Vector3Int>();
        for (int i = 0; i < pos.Length; i++)
        {
            Vector3 nn = hasNormals ? nor[i].normalized : Vector3.back;
            var k = new Vector3Int(Mathf.RoundToInt(nn.x * 100), Mathf.RoundToInt(nn.y * 100), Mathf.RoundToInt(nn.z * 100));
            int g = keys.IndexOf(k);
            if (g < 0) { keys.Add(k); g = keys.Count - 1; }
            groupOf[i] = g;
        }
        var area = new float[keys.Count];
        for (int i = 0; i + 2 < tri.Length; i += 3)
        {
            int a = tri[i], b = tri[i + 1], c = tri[i + 2];
            if (groupOf[a] != groupOf[b] || groupOf[a] != groupOf[c]) continue;
            Vector3 A = t.TransformPoint(pos[a]), B = t.TransformPoint(pos[b]), C = t.TransformPoint(pos[c]);
            area[groupOf[a]] += Vector3.Cross(B - A, C - A).magnitude * 0.5f;
        }
        float maxArea = 0;
        foreach (var x in area) maxArea = Mathf.Max(maxArea, x);
        int best = -1;
        float bestDot = -2;
        for (int g = 0; g < keys.Count; g++)
        {
            if (area[g] < maxArea * 0.9f) continue;
            float d = Vector3.Dot(((Vector3)keys[g]).normalized, Vector3.back);
            if (d > bestDot) { bestDot = d; best = g; }
        }
        if (best < 0) return false;

        var P = new List<Vector3>();
        var S = new List<Vector2>();
        for (int i = 0; i < pos.Length; i++)
            if (groupOf[i] == best) { P.Add(pos[i]); S.Add(uv[i]); }
        if (!FitAffine(P, S, out o, out u, out v)) return false;
        n = ((Vector3)keys[best]).normalized;
        int big = 0;
        foreach (var x in area) if (x >= maxArea * 0.9f) big++;
        info = $"{keys.Count} face(s); using the one with local normal {n} " +
               (big > 1 ? $"(one of {big} equally large faces: the one facing local -Z)" : "(the largest)");
        return u.sqrMagnitude > 0 && v.sqrMagnitude > 0;
    }

    // Least-squares fit of position = o + u*s + v*t over the given vertices.
    static bool FitAffine(List<Vector3> pos, List<Vector2> uv, out Vector3 o, out Vector3 u, out Vector3 v)
    {
        o = u = v = Vector3.zero;
        if (pos.Count < 3) return false;
        double s00 = 0, s01 = 0, s02 = 0, s11 = 0, s12 = 0, s22 = 0;
        Vector3 b0 = Vector3.zero, b1 = Vector3.zero, b2 = Vector3.zero;
        for (int i = 0; i < pos.Count; i++)
        {
            double s = uv[i].x, t = uv[i].y;
            s00 += 1; s01 += s; s02 += t; s11 += s * s; s12 += s * t; s22 += t * t;
            b0 += pos[i]; b1 += pos[i] * (float)s; b2 += pos[i] * (float)t;
        }
        double[,] M = { { s00, s01, s02 }, { s01, s11, s12 }, { s02, s12, s22 } };
        double det = M[0, 0] * (M[1, 1] * M[2, 2] - M[1, 2] * M[2, 1])
                   - M[0, 1] * (M[1, 0] * M[2, 2] - M[1, 2] * M[2, 0])
                   + M[0, 2] * (M[1, 0] * M[2, 1] - M[1, 1] * M[2, 0]);
        if (System.Math.Abs(det) < 1e-12) return false;
        var inv = new double[3, 3];
        for (int r = 0; r < 3; r++)
            for (int c = 0; c < 3; c++)
            {
                int r1 = (c + 1) % 3, r2 = (c + 2) % 3, c1 = (r + 1) % 3, c2 = (r + 2) % 3;
                inv[r, c] = (M[r1, c1] * M[r2, c2] - M[r1, c2] * M[r2, c1]) / det;
            }
        Vector3[] B = { b0, b1, b2 };
        var X = new Vector3[3];
        for (int r = 0; r < 3; r++)
            X[r] = B[0] * (float)inv[r, 0] + B[1] * (float)inv[r, 1] + B[2] * (float)inv[r, 2];
        o = X[0]; u = X[1]; v = X[2];
        return true;
    }
}
