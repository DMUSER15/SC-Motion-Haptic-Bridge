// MiniJson - tiny JSON reader, built-in .NET only (no Newtonsoft), so SimHub
// updates that change bundled libraries cannot break the plugin.
// Objects -> Dictionary<string, object>, arrays -> List<object>, numbers -> double,
// strings -> string, true/false -> bool, null -> null.
// Also accepts Python's non-standard NaN / Infinity / -Infinity (json.dumps default).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace StarCitizenHaptics
{
    public static class MiniJson
    {
        public static object Parse(string s)
        {
            if (s == null) throw new FormatException("null input");
            int i = 0;
            object v = Value(s, ref i);
            Ws(s, ref i);
            if (i != s.Length) throw new FormatException("trailing data at " + i);
            return v;
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

        static object Value(string s, ref int i)
        {
            Ws(s, ref i);
            if (i >= s.Length) throw new FormatException("unexpected end");
            char c = s[i];
            if (c == '{') return Obj(s, ref i);
            if (c == '[') return Arr(s, ref i);
            if (c == '"') return Str(s, ref i);
            if (Lit(s, ref i, "true")) return true;
            if (Lit(s, ref i, "false")) return false;
            if (Lit(s, ref i, "null")) return null;
            if (Lit(s, ref i, "NaN")) return double.NaN;
            if (Lit(s, ref i, "Infinity")) return double.PositiveInfinity;
            if (Lit(s, ref i, "-Infinity")) return double.NegativeInfinity;
            return Num(s, ref i);
        }

        static bool Lit(string s, ref int i, string word)
        {
            if (string.CompareOrdinal(s, i, word, 0, word.Length) == 0)
            {
                i += word.Length;
                return true;
            }
            return false;
        }

        static Dictionary<string, object> Obj(string s, ref int i)
        {
            var d = new Dictionary<string, object>();
            i++; // {
            Ws(s, ref i);
            if (i < s.Length && s[i] == '}') { i++; return d; }
            while (true)
            {
                Ws(s, ref i);
                if (i >= s.Length || s[i] != '"') throw new FormatException("expected key at " + i);
                string k = Str(s, ref i);
                Ws(s, ref i);
                if (i >= s.Length || s[i] != ':') throw new FormatException("expected ':' at " + i);
                i++;
                d[k] = Value(s, ref i);
                Ws(s, ref i);
                if (i >= s.Length) throw new FormatException("unterminated object");
                if (s[i] == ',') { i++; continue; }
                if (s[i] == '}') { i++; return d; }
                throw new FormatException("expected ',' or '}' at " + i);
            }
        }

        static List<object> Arr(string s, ref int i)
        {
            var a = new List<object>();
            i++; // [
            Ws(s, ref i);
            if (i < s.Length && s[i] == ']') { i++; return a; }
            while (true)
            {
                a.Add(Value(s, ref i));
                Ws(s, ref i);
                if (i >= s.Length) throw new FormatException("unterminated array");
                if (s[i] == ',') { i++; continue; }
                if (s[i] == ']') { i++; return a; }
                throw new FormatException("expected ',' or ']' at " + i);
            }
        }

        static string Str(string s, ref int i)
        {
            i++; // opening quote
            StringBuilder sb = null;
            int start = i;
            while (i < s.Length)
            {
                char c = s[i];
                if (c == '"')
                {
                    string r = sb == null ? s.Substring(start, i - start) : sb.Append(s, start, i - start).ToString();
                    i++;
                    return r;
                }
                if (c == '\\')
                {
                    if (sb == null) sb = new StringBuilder();
                    sb.Append(s, start, i - start);
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
                    start = i;
                    continue;
                }
                i++;
            }
            throw new FormatException("unterminated string");
        }

        static double Num(string s, ref int i)
        {
            int start = i;
            while (i < s.Length)
            {
                char c = s[i];
                if ((c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.' || c == 'e' || c == 'E') i++;
                else break;
            }
            if (i == start) throw new FormatException("unexpected character '" + s[i] + "' at " + i);
            return double.Parse(s.Substring(start, i - start), NumberStyles.Float, CultureInfo.InvariantCulture);
        }

        // ---- helpers for reading parsed values ----

        public static bool TryNumber(object o, out double d)
        {
            if (o is double) { d = (double)o; return true; }
            if (o is bool) { d = (bool)o ? 1.0 : 0.0; return true; }
            d = 0.0;
            return false;
        }

        /// Value for field id in a capture-server packet {"m":[ids],"v":[values]}; null if absent.
        public static object Field(Dictionary<string, object> pkt, int id)
        {
            object mo, vo;
            if (!pkt.TryGetValue("m", out mo) || !pkt.TryGetValue("v", out vo)) return null;
            var m = mo as List<object>;
            var v = vo as List<object>;
            if (m == null || v == null || m.Count != v.Count) return null;
            for (int k = 0; k < m.Count; k++)
            {
                double idd;
                if (TryNumber(m[k], out idd) && (int)idd == id) return v[k];
            }
            return null;
        }
    }
}
