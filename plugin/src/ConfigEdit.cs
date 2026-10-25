// ConfigEdit - writes intensity changes (master, per-effect gain) into effects.json for the
// settings page sliders (0.4.0). Built-in .NET only, no SimHub types, so it can be tested
// outside SimHub.
//
// Rules:
//   - Only the number after "master" or after one effect's "gain" changes; every other byte of
//     the file (layout, notes, key order, line endings, BOM) stays as it was. A missing key is
//     inserted right after the opening brace.
//   - The edited text must still parse (FxConfig.Parse) without new errors and must give the
//     wanted value back; otherwise nothing is written.
//   - The file is rewritten in place (opened, overwritten, cut to length). It is never replaced
//     or renamed, so a symbolic link (Linux: ~/sc_haptics/effects.json -> the repository's
//     config/effects.json) stays a link and the repository file receives the change.
//   - Before the first write of a SimHub session the untouched file is copied to
//     <folder of effects.json>\backup\effects.json.<yyyyMMdd_HHmmss>.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;

namespace StarCitizenHaptics
{
    static class ConfigEdit
    {
        public const double MinGain = 0, MaxGain = 100;

        /// Slider value -> the number written: clamped to 0..100, one decimal, invariant culture.
        public static string Fmt(double v)
        {
            if (double.IsNaN(v)) v = 0;
            v = Math.Max(MinGain, Math.Min(MaxGain, v));
            v = Math.Round(v, 1, MidpointRounding.AwayFromZero);
            return v.ToString("0.#", CultureInfo.InvariantCulture);
        }

        /// New text with master (effect == null or "") or the effect's gain set to value.
        /// Throws FormatException when the file's structure does not allow the edit.
        public static string SetGain(string text, string effect, double value)
        {
            string num = Fmt(value);
            int i = 0;
            Node root = Value(text, ref i);
            Ws(text, ref i);
            if (i != text.Length) throw new FormatException("trailing data at " + i);
            if (root.Kind != 'o') throw new FormatException("top level must be an object");
            Node target, holder;
            string key;
            if (string.IsNullOrEmpty(effect))
            {
                holder = root;
                key = "master";
            }
            else
            {
                Node list;
                if (!root.Members.TryGetValue("effects", out list) || list.Kind != 'a')
                    throw new FormatException("no effects list");
                holder = null;
                foreach (Node e in list.Items)
                {
                    Node nm;
                    if (e.Kind == 'o' && e.Members.TryGetValue("name", out nm) && nm.Kind == 's' && nm.Str == effect)
                    {
                        holder = e;      // first one wins, as in FxConfig.Parse
                        break;
                    }
                }
                if (holder == null) throw new FormatException("no effect named '" + effect + "'");
                key = "gain";
            }
            if (holder.Members.TryGetValue(key, out target))
            {
                if (target.Kind != 'n') throw new FormatException("\"" + key + "\" is not a number");
                return text.Substring(0, target.Start) + num + text.Substring(target.End);
            }
            int at = holder.Start + 1;   // just after '{'
            return text.Substring(0, at) + "\"" + key + "\": " + num + ", " + text.Substring(at);
        }

        /// Reads the file, applies SetGain, checks the result and writes it back in place.
        /// backedUp: false before the first write of this session (a backup copy is made, then
        /// it becomes true). Returns a short text for the status page. Throws on failure; the
        /// file is unchanged then.
        public static string WriteGain(string path, string effect, double value, string backupDir, ref bool backedUp)
        {
            byte[] raw = File.ReadAllBytes(path);
            bool bom = raw.Length >= 3 && raw[0] == 0xEF && raw[1] == 0xBB && raw[2] == 0xBF;
            var utf8 = new UTF8Encoding(false, true);
            string text = utf8.GetString(raw, bom ? 3 : 0, raw.Length - (bom ? 3 : 0));
            string what = string.IsNullOrEmpty(effect) ? "master" : effect;

            string edited = SetGain(text, effect, value);
            if (edited == text) return what + " already " + Fmt(value);

            FxConfig before = FxConfig.Parse(text);
            FxConfig after = FxConfig.Parse(edited);
            if (after.Fatal) throw new FormatException("edit would break the file: " + (after.Errors.Count > 0 ? after.Errors[0] : "?"));
            if (!before.Fatal && after.Errors.Count > before.Errors.Count)
                throw new FormatException("edit would add a config error: " + after.Errors[after.Errors.Count - 1]);
            double want = double.Parse(Fmt(value), CultureInfo.InvariantCulture), got = double.NaN;
            if (string.IsNullOrEmpty(effect)) got = after.Master;
            else foreach (FxDef d in after.Effects) if (d.Name == effect) { got = d.Gain; break; }
            if (double.IsNaN(got) || Math.Abs(got - want) > 1e-9)
                throw new FormatException("edit did not give " + what + " = " + Fmt(value) + " (effect skipped by the parser?)");

            if (!backedUp)
            {
                Directory.CreateDirectory(backupDir);
                string b = Path.Combine(backupDir, Path.GetFileName(path) + "." + DateTime.Now.ToString("yyyyMMdd_HHmmss", CultureInfo.InvariantCulture));
                if (!File.Exists(b)) File.WriteAllBytes(b, raw);
                backedUp = true;
            }

            byte[] body = utf8.GetBytes(edited);
            byte[] data = body;
            if (bom)
            {
                data = new byte[body.Length + 3];
                data[0] = 0xEF; data[1] = 0xBB; data[2] = 0xBF;
                Buffer.BlockCopy(body, 0, data, 3, body.Length);
            }
            // in place: FileMode.Open never creates or replaces the file, so a link stays a link
            using (var fs = new FileStream(path, FileMode.Open, FileAccess.Write, FileShare.Read))
            {
                fs.Write(data, 0, data.Length);
                fs.SetLength(data.Length);
                fs.Flush(true);
            }
            return what + " = " + Fmt(value) + " saved";
        }

        // ---- a JSON reader that keeps positions (same grammar as MiniJson) ----

        sealed class Node
        {
            public char Kind;          // o object, a array, s string, n number, l literal
            public int Start, End;     // text[Start..End) is the value
            public string Str;
            public Dictionary<string, Node> Members;
            public List<Node> Items;
        }

        static void Ws(string s, ref int i)
        {
            while (i < s.Length)
            {
                char c = s[i];
                if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '﻿') i++;
                else break;
            }
        }

        static Node Value(string s, ref int i)
        {
            Ws(s, ref i);
            if (i >= s.Length) throw new FormatException("unexpected end");
            char c = s[i];
            if (c == '{') return Obj(s, ref i);
            if (c == '[') return Arr(s, ref i);
            if (c == '"') { int st = i; string v = Str(s, ref i); return new Node { Kind = 's', Start = st, End = i, Str = v }; }
            foreach (string w in new[] { "true", "false", "null", "NaN", "Infinity", "-Infinity" })
                if (string.CompareOrdinal(s, i, w, 0, w.Length) == 0)
                {
                    var n = new Node { Kind = 'l', Start = i, End = i + w.Length };
                    i += w.Length;
                    return n;
                }
            int start = i;
            while (i < s.Length)
            {
                char d = s[i];
                if ((d >= '0' && d <= '9') || d == '-' || d == '+' || d == '.' || d == 'e' || d == 'E') i++;
                else break;
            }
            if (i == start) throw new FormatException("unexpected character '" + s[i] + "' at " + i);
            double tmp;
            if (!double.TryParse(s.Substring(start, i - start), NumberStyles.Float, CultureInfo.InvariantCulture, out tmp))
                throw new FormatException("bad number at " + start);
            return new Node { Kind = 'n', Start = start, End = i };
        }

        static Node Obj(string s, ref int i)
        {
            var n = new Node { Kind = 'o', Start = i, Members = new Dictionary<string, Node>() };
            i++;
            Ws(s, ref i);
            if (i < s.Length && s[i] == '}') { i++; n.End = i; return n; }
            while (true)
            {
                Ws(s, ref i);
                if (i >= s.Length || s[i] != '"') throw new FormatException("expected key at " + i);
                string k = Str(s, ref i);
                Ws(s, ref i);
                if (i >= s.Length || s[i] != ':') throw new FormatException("expected ':' at " + i);
                i++;
                Node v = Value(s, ref i);
                if (!n.Members.ContainsKey(k)) n.Members[k] = v;   // MiniJson keeps the last; a duplicate key is refused below
                else throw new FormatException("duplicate key \"" + k + "\" at " + v.Start);
                Ws(s, ref i);
                if (i >= s.Length) throw new FormatException("unterminated object");
                if (s[i] == ',') { i++; continue; }
                if (s[i] == '}') { i++; n.End = i; return n; }
                throw new FormatException("expected ',' or '}' at " + i);
            }
        }

        static Node Arr(string s, ref int i)
        {
            var n = new Node { Kind = 'a', Start = i, Items = new List<Node>() };
            i++;
            Ws(s, ref i);
            if (i < s.Length && s[i] == ']') { i++; n.End = i; return n; }
            while (true)
            {
                n.Items.Add(Value(s, ref i));
                Ws(s, ref i);
                if (i >= s.Length) throw new FormatException("unterminated array");
                if (s[i] == ',') { i++; continue; }
                if (s[i] == ']') { i++; n.End = i; return n; }
                throw new FormatException("expected ',' or ']' at " + i);
            }
        }

        static string Str(string s, ref int i)
        {
            i++;
            var sb = new StringBuilder();
            while (i < s.Length)
            {
                char c = s[i];
                if (c == '"') { i++; return sb.ToString(); }
                if (c == '\\')
                {
                    i++;
                    if (i >= s.Length) break;
                    char e = s[i];
                    switch (e)
                    {
                        case '"': sb.Append('"'); break;
                        case '\\': sb.Append('\\'); break;
                        case '/': sb.Append('/'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case 'n': sb.Append('\n'); break;
                        case 'r': sb.Append('\r'); break;
                        case 't': sb.Append('\t'); break;
                        case 'u':
                            if (i + 4 >= s.Length) throw new FormatException("bad \\u escape");
                            sb.Append((char)int.Parse(s.Substring(i + 1, 4), NumberStyles.HexNumber, CultureInfo.InvariantCulture));
                            i += 4;
                            break;
                        default: throw new FormatException("bad escape at " + i);
                    }
                    i++;
                    continue;
                }
                sb.Append(c);
                i++;
            }
            throw new FormatException("unterminated string");
        }
    }
}
