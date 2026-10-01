// Sweep-code evaluation and schedule display (grammar: data/README.md). Tiles
// and /check carry raw codes; checkDaySweeping gives today/tomorrow/safe
// against a region-local "now". Tested by tests/test_urgency.py.
(function (global) {
  'use strict';

  // Same tables as analysis.WEEKDAY_CODES / NO_SWEEP_CODES.
  var WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  var WEEKDAY_CODES = {};
  ['M', 'T', 'W', 'TH', 'F', 'S', 'SU'].forEach(function (t, i) { WEEKDAY_CODES[t] = [i, WEEKDAYS[i]]; });
  var NO_SWEEP_CODES = [
    'N', 'NS', 'O', 'N-S', 'N-E', 'N-O',
    'NS-UC', 'NS-H', 'NS-O', 'NS-A',
    'MS', 'DM', 'MISSING',
  ];
  var NO_SWEEP = {};
  NO_SWEEP_CODES.forEach(function (c) { NO_SWEEP[c] = 1; });
  var CODE_RE = /^((?:TH|SU|M|T|W|F|S)*)(E?|[1-5]+)$/;
  var DAY_TOKEN_RE = /TH|SU|M|T|W|F|S/g;

  function isNoSweepCode(code) {
    return typeof code === 'string' && NO_SWEEP[code.trim().toUpperCase()] === 1;
  }

  // ── Date helpers (calendar arithmetic only; no JS Date tz pitfalls) ──────────
  // Days are {y, m, d}. Python weekday(): Mon=0..Sun=6; JS getUTCDay(): Sun=0.
  function pyWeekday(y, m, d) { return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7; }

  function dayKey(y, m, d) { return y * 10000 + m * 100 + d; }

  function addDays(p, n) {
    var dt = new Date(Date.UTC(p.y, p.m - 1, p.d + n));
    return { y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate() };
  }
  function addOneDay(y, m, d) { return addDays({ y: y, m: m, d: d }, 1); }

  // ── Sweep-code rules ─────────────────────────────────────────────────────────
  var _partsCache = {};
  // {days: [token...], suffix} for a weekly code, or null.
  function codeParts(code) {
    if (Object.prototype.hasOwnProperty.call(_partsCache, code)) return _partsCache[code];
    var c = code.trim().toUpperCase(), out = null;
    if (!NO_SWEEP[c]) {
      var m = CODE_RE.exec(c);
      if (m && (m[1] || m[2] === 'E')) out = { days: m[1].match(DAY_TOKEN_RE) || [], suffix: m[2] };
    }
    _partsCache[code] = out;
    return out;
  }

  var _ruleCache = {};
  // {weekdays: {wd:1}, ordinals: {n:1}|null, dates: {dayKey:1}|null}, or null.
  function codeRule(code) {
    if (Object.prototype.hasOwnProperty.call(_ruleCache, code)) return _ruleCache[code];
    var rule = null, i;
    var dates = parseDatesCode(code);
    if (dates !== null) {
      var set = {};
      for (i = 0; i < dates.length; i++) set[dayKey(dates[i].y, dates[i].m, dates[i].d)] = 1;
      rule = { weekdays: null, ordinals: null, dates: set };
    } else {
      var p = codeParts(code);
      if (p) {
        var wds = {};
        if (p.days.length) for (i = 0; i < p.days.length; i++) wds[WEEKDAY_CODES[p.days[i]][0]] = 1;
        else for (i = 0; i < 7; i++) wds[i] = 1;
        var ords = null;
        if (/^[1-5]+$/.test(p.suffix)) {
          ords = {};
          for (i = 0; i < p.suffix.length; i++) ords[+p.suffix.charAt(i)] = 1;
        }
        rule = { weekdays: wds, ordinals: ords, dates: null };
      }
    }
    _ruleCache[code] = rule;
    return rule;
  }

  function sweepsOn(code, y, m, d) {
    if (typeof code !== 'string') return false;
    var r = codeRule(code);
    if (!r) return false;
    if (r.dates) return r.dates[dayKey(y, m, d)] === 1;
    if (!r.weekdays[pyWeekday(y, m, d)]) return false;
    return !r.ordinals || r.ordinals[Math.floor((d - 1) / 7) + 1] === 1;
  }

  // Sweep dates of `code` in [start, end] inclusive ({y,m,d} objects).
  function datesInRange(code, start, end) {
    var out = [], cur = start, endK = dayKey(end.y, end.m, end.d);
    while (dayKey(cur.y, cur.m, cur.d) <= endK) {
      if (sweepsOn(code, cur.y, cur.m, cur.d)) out.push(cur);
      cur = addOneDay(cur.y, cur.m, cur.d);
    }
    return out;
  }

  // Both sides' sweeps in [start, end]:
  // [{y, m, d, items: [{side: 'even'|'odd', time}]}] date-sorted.
  function sweepDays(even, odd, start, end) {
    var byKey = {};
    [['even', even || []], ['odd', odd || []]].forEach(function (so) {
      so[1].forEach(function (e) {
        var time = (typeof e[2] === 'string' && e[2].trim()) ? e[2] : '';
        datesInRange(e[0], start, end).forEach(function (d) {
          var k = dayKey(d.y, d.m, d.d);
          var day = byKey[k] || (byKey[k] = { y: d.y, m: d.m, d: d.d, items: [] });
          var dup = day.items.some(function (it) { return it.side === so[0] && it.time === time; });
          if (!dup) day.items.push({ side: so[0], time: time });
        });
      });
    });
    return Object.keys(byKey).map(Number).sort(function (a, b) { return a - b; })
      .map(function (k) { return byKey[k]; });
  }

  // ── Urgency verdict ──────────────────────────────────────────────────────────
  // entries: [{code, time}, ...]; now: {y, m, d, min} (min = minutes since
  // region-local midnight). Returns 'today' | 'tomorrow' | 'safe'.
  function checkDaySweeping(entries, now) {
    var tmr = addOneDay(now.y, now.m, now.d);
    var todayEnds = [], sweptTomorrow = false;
    for (var i = 0; i < entries.length; i++) {
      var code = entries[i].code;
      if (sweepsOn(code, now.y, now.m, now.d)) todayEnds.push(parseEndMinutes(entries[i].time || ''));
      if (sweepsOn(code, tmr.y, tmr.m, tmr.d)) sweptTomorrow = true;
    }
    for (var t = 0; t < todayEnds.length; t++) {
      // null end = untimed or unparseable window: open all day.
      if (todayEnds[t] === null || now.min <= todayEnds[t]) return 'today';
    }
    return sweptTomorrow ? 'tomorrow' : 'safe';
  }

  // Build a `now` from a JS Date interpreted in the region's IANA tz.
  function nowForTimeZone(tz, when) {
    var d = when || new Date();
    var parts;
    try {
      var fmt = new Intl.DateTimeFormat('en-US', {
        timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', hour12: false,
      });
      parts = {};
      fmt.formatToParts(d).forEach(function (p) { parts[p.type] = p.value; });
    } catch (e) {
      parts = {
        year: d.getFullYear(), month: d.getMonth() + 1, day: d.getDate(),
        hour: d.getHours(), minute: d.getMinutes(),
      };
    }
    var hour = parseInt(parts.hour, 10) % 24; // Intl may emit "24" at midnight
    return {
      y: parseInt(parts.year, 10), m: parseInt(parts.month, 10),
      d: parseInt(parts.day, 10), min: hour * 60 + parseInt(parts.minute, 10),
    };
  }

  // ── 'DATES:' codes: parsing and next sweep cluster ───────────────────────────
  var MONTH_ABBR = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function parseDatesCode(code) {
    if (typeof code !== 'string' || code.toUpperCase().indexOf('DATES:') !== 0) return null;
    var out = [];
    var parts = code.slice(6).split(',');
    for (var i = 0; i < parts.length; i++) {
      var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(parts[i].trim());
      if (m) out.push({ y: +m[1], m: +m[2], d: +m[3] });
    }
    out.sort(function (a, b) { return dayKey(a.y, a.m, a.d) - dayKey(b.y, b.m, b.d); });
    return out;
  }

  function diffDays(a, b) {
    return Math.round((Date.UTC(b.y, b.m - 1, b.d) - Date.UTC(a.y, a.m - 1, a.d)) / 86400000);
  }

  function clusterDates(dates, maxGap) {
    maxGap = maxGap || 4;  // a zone's sides are swept a few days apart
    var clusters = [], cur = [];
    for (var i = 0; i < dates.length; i++) {
      if (cur.length && diffDays(cur[cur.length - 1], dates[i]) > maxGap) {
        clusters.push(cur); cur = [];
      }
      cur.push(dates[i]);
    }
    if (cur.length) clusters.push(cur);
    return clusters;
  }

  function formatDatesByMonth(dates) {
    var grouped = {}, order = [];
    for (var i = 0; i < dates.length; i++) {
      var d = dates[i], k = d.y + '-' + d.m;
      if (!grouped[k]) { grouped[k] = []; order.push([k, d.m]); }
      grouped[k].push(d.d);
    }
    return order.map(function (o) {
      return MONTH_ABBR[o[1]] + ' ' + grouped[o[0]].join(', ');
    }).join('; ');
  }

  // Dates of the next cluster for a DATES code; null for non-DATES, [] when none.
  function nextClusterDates(code, now, maxDates) {
    maxDates = maxDates || 3;
    var dates = parseDatesCode(code);
    if (dates === null) return null;
    var todayK = dayKey(now.y, now.m, now.d);
    var future = dates.filter(function (d) { return dayKey(d.y, d.m, d.d) >= todayK; });
    if (!future.length) return [];
    return clusterDates(future)[0].slice(0, maxDates);
  }

  // ── Schedule display (card, hover and center banner) ────────────────────────
  // Time parsing is lenient (same pattern as normalize._TIME_RANGE_RE): tolerates the
  // PDF artifacts in raw tile data — bullet/middot/"o" separators, stray spaces
  // inside hours/minutes and around colons, "A M"/"P,M".
  var _DISP_SRC =
    '(\\d\\s*\\d?)\\s*(?::\\s*(\\d\\s*\\d))?\\s*(A[\\s,]*M|P[\\s,]*M)' +
    '[\\s,]*(?:[-–—•·o]|to)\\s*' +
    '(\\d\\s*\\d?)\\s*(?::\\s*(\\d\\s*\\d))?\\s*(A[\\s,]*M|P[\\s,]*M)';
  var TIME_RANGE_DISP_RE   = new RegExp(_DISP_SRC, 'i');
  var TIME_RANGE_DISP_RE_G = new RegExp(_DISP_SRC, 'ig');
  var WEEKDAY_CANON = {
    MON: [0, 'Mon'], MONDAY: [0, 'Mon'],
    TUE: [1, 'Tue'], TUES: [1, 'Tue'], TUESDAY: [1, 'Tue'],
    WED: [2, 'Wed'], WEDS: [2, 'Wed'], WEDNESDAY: [2, 'Wed'],
    THU: [3, 'Thu'], THUR: [3, 'Thu'], THURS: [3, 'Thu'], THURSDAY: [3, 'Thu'],
    FRI: [4, 'Fri'], FRIDAY: [4, 'Fri'],
    SAT: [5, 'Sat'], SATURDAY: [5, 'Sat'],
    SUN: [6, 'Sun'], SUNDAY: [6, 'Sun'],
  };

  // [hour, minute, 'AM'|'PM'] minus stray spaces.
  function _clockParts(h, mn, ap) {
    return [parseInt(h.replace(/\s+/g, ''), 10), mn ? parseInt(mn.replace(/\s+/g, ''), 10) : 0,
            ap.replace(/[\s,]+/g, '').toUpperCase()];
  }
  function _fmtPart(h, mn, ap) {
    var p = _clockParts(h, mn, ap);
    return p[1] ? (p[0] + ':' + (p[1] < 10 ? '0' + p[1] : p[1]) + p[2]) : (p[0] + p[2]);
  }
  // Minutes since midnight, or null when out of range.
  function _toMinutes(h, mn, ap) {
    var p = _clockParts(h, mn, ap);
    if (p[0] < 1 || p[0] > 12 || p[1] > 59) return null;
    return (p[0] % 12 + (p[2] === 'PM' ? 12 : 0)) * 60 + p[1];
  }
  // A window's end in minutes since midnight, null when the
  // window is unparseable. A window ending at 12AM runs to the end of the day.
  function parseEndMinutes(timeStr) {
    var m = typeof timeStr === 'string' ? TIME_RANGE_DISP_RE.exec(timeStr) : null;
    if (!m) return null;
    var start = _toMinutes(m[1], m[2], m[3]), end = _toMinutes(m[4], m[5], m[6]);
    if (start === null || end === null) return null;
    return end || 24 * 60 - 1;
  }
  function timeDisplay(raw) {
    if (typeof raw !== 'string') return 'N/A';
    var s = raw.trim();
    if (s === '' || /^(n\/a|na|none|nan)$/i.test(s)) return 'N/A';
    var m = TIME_RANGE_DISP_RE.exec(s);
    if (!m) return s;
    return _fmtPart(m[1], m[2], m[3]) + '–' + _fmtPart(m[4], m[5], m[6]);
  }

  function _wdCanon(tok) { return WEEKDAY_CANON[tok.replace(/[.,]/g, '').toUpperCase()]; }
  function hasWeekday(desc) { return desc.split(' ').some(function (t) { return !!_wdCanon(t); }); }

  function weekdayFirst(desc) {
    var toks = desc.split(' ').filter(Boolean);
    if (!toks.length) return desc;
    var every = toks[0].toLowerCase() === 'every';
    var body = every ? toks.slice(1) : toks;
    var wd = [];
    for (var i = 0; i < body.length; i++) if (_wdCanon(body[i])) wd.push(i);
    if (!wd.length) return desc;
    var lo = wd[0], hi = wd[wd.length - 1];
    // Only a contiguous run of weekdays joined by "&" moves as one list.
    for (var j = lo; j <= hi; j++) {
      if (wd.indexOf(j) < 0 && body[j] !== '&') { hi = lo; break; }
    }
    var days = [];
    for (var k = lo; k <= hi; k++) if (wd.indexOf(k) >= 0) days.push(_wdCanon(body[k])[1]);
    var daysS = days.length === 1 ? days[0]
              : days.slice(0, -1).join(', ') + ' & ' + days[days.length - 1];
    var rest = body.slice(0, lo).concat(body.slice(hi + 1));
    return (every ? ['Every'] : []).concat([daysS]).concat(rest).join(' ').trim();
  }

  // Bare week-of-month ordinal runs (1–5 only) -> "1st & 3rd". A leading
  // capture instead of lookbehind, for older mobile browsers.
  function _prettyOrdinals(text) {
    return text.replace(/(^|[^\w])([1-5]{1,5})(?![\w])/g, function (_m, pre, run) {
      var parts = [];
      for (var i = 0; i < run.length; i++) {
        var d = run.charAt(i);
        parts.push(d + (d === '1' ? 'st' : d === '2' ? 'nd' : d === '3' ? 'rd' : 'th'));
      }
      var out = parts.length === 1 ? parts[0]
              : parts.slice(0, -1).join(', ') + ' & ' + parts[parts.length - 1];
      return pre + out;
    });
  }

  function sweepBody(desc, time) {
    var d = (typeof desc === 'string') ? desc : '';
    d = d.replace(/\s*\((?:every|bi-?weekly)\)/ig, '');
    d = d.replace(TIME_RANGE_DISP_RE_G, '');
    d = d.replace(/\s*\bof\s+(?:the\s+)?month\b/ig, '');
    d = d.replace(/\band\b/ig, '&');
    d = d.replace(/\s+/g, ' ').replace(/^[\s,]+|[\s,]+$/g, '').trim();
    // Weekday schedules only: "2 lines" must not become "2nd lines".
    if (hasWeekday(d)) d = _prettyOrdinals(weekdayFirst(d));
    if (!d || d.toUpperCase() === 'N/A') return '';
    var t = timeDisplay(time || '');
    if (t === '' || t === 'N/A' || d.indexOf(t) !== -1) return d;
    return d + ', ' + t;
  }

  function codeWeekday(code) {
    var p = codeParts(code);
    return (p && p.days.length) ? WEEKDAY_CODES[p.days[0]] : null;
  }

  function codeOrdinals(code) {
    var p = codeParts(code), s = {};
    if (p && /^[1-5]+$/.test(p.suffix)) {
      for (var i = 0; i < p.suffix.length; i++) s[+p.suffix.charAt(i)] = 1;
    }
    return s;
  }

  function hasAllFour(ords) {
    return ords[1] && ords[2] && ords[3] && ords[4];
  }

  // Split sorted weekday ranks into runs of consecutive days.
  function contiguousRuns(ranks) {
    var runs = [], cur = [];
    for (var i = 0; i < ranks.length; i++) {
      if (cur.length && ranks[i] === cur[cur.length - 1] + 1) cur.push(ranks[i]);
      else { if (cur.length) runs.push(cur); cur = [ranks[i]]; }
    }
    if (cur.length) runs.push(cur);
    return runs;
  }

  function weekdayRangeBody(label, time) {
    var t = timeDisplay(time || '');
    return (t && t !== 'N/A') ? (label + ', ' + t) : label;
  }

  function _ent(e) {
    if (Array.isArray(e)) return { code: e[0], desc: e[1] || '', time: e[2] || '' };
    return { code: e.code, desc: e.desc || '', time: e.time || '' };
  }

  // entries: array of {code,desc,time} or [code,desc,time]. Returns display lines.
  function formatScheduleSide(entries, now) {
    var clean = [], i;
    for (i = 0; i < (entries || []).length; i++) {
      var e = _ent(entries[i]);
      if (typeof e.code !== 'string' || isNoSweepCode(e.code)) continue;
      clean.push(e);
    }
    var datesE = [], weekly = [];
    for (i = 0; i < clean.length; i++) {
      (parseDatesCode(clean[i].code) !== null ? datesE : weekly).push(clean[i]);
    }

    var groups = {}, order = [], loose = [];
    for (i = 0; i < weekly.length; i++) {
      var w = weekly[i], wk = codeWeekday(w.code);
      if (wk === null) { loose.push(w); continue; }
      var key = wk[0] + '|' + timeDisplay(w.time || '');
      if (!groups[key]) { groups[key] = { rank: wk[0], disp: wk[1], ords: {}, time: w.time, items: [] }; order.push(key); }
      var ords = codeOrdinals(w.code);
      for (var o in ords) groups[key].ords[o] = 1;
      groups[key].items.push(w);
    }

    // Collapse every-week groups that
    // share one time across a contiguous 3+ weekday run into "Mon–Fri, <time>"
    // (all seven -> "Every day, <time>"); partial-ordinal groups stay per-day.
    var ranked = [];
    var everyweek = {};   // td -> {time, days: {rank: [disp, fallback]}}
    var ewOrder = [];
    for (i = 0; i < order.length; i++) {
      var g = groups[order[i]];
      var ordCount = 0;
      for (var ok in g.ords) if (g.ords[ok]) ordCount++;
      if (ordCount === 0 || hasAllFour(g.ords)) {
        var fallback = hasAllFour(g.ords)
          ? sweepBody('Every ' + g.disp, g.time)
          : sweepBody(g.items[0].desc, g.items[0].time);
        var td = timeDisplay(g.time || '');
        if (!everyweek[td]) { everyweek[td] = { time: g.time, days: {} }; ewOrder.push(td); }
        everyweek[td].days[g.rank] = [g.disp, fallback];
      } else {
        for (var j = 0; j < g.items.length; j++) ranked.push([g.rank, 0, sweepBody(g.items[j].desc, g.items[j].time)]);
      }
    }
    for (i = 0; i < ewOrder.length; i++) {
      var slot = everyweek[ewOrder[i]];
      var ordRanks = Object.keys(slot.days).map(Number).sort(function (a, b) { return a - b; });
      if (ordRanks.length === 7) {
        ranked.push([0, -1, weekdayRangeBody('Every day', slot.time)]);
        continue;
      }
      var runs = contiguousRuns(ordRanks);
      for (var ri = 0; ri < runs.length; ri++) {
        var run = runs[ri];
        if (run.length >= 3) {
          var label = slot.days[run[0]][0] + '–' + slot.days[run[run.length - 1]][0];
          ranked.push([run[0], -1, weekdayRangeBody(label, slot.time)]);
        } else {
          for (var rr = 0; rr < run.length; rr++) ranked.push([run[rr], -1, slot.days[run[rr]][1]]);
        }
      }
    }
    ranked.sort(function (a, b) { return (a[0] - b[0]) || (a[1] - b[1]); });

    var lines = [], seen = {};
    for (i = 0; i < ranked.length; i++) {
      var b = ranked[i][2];
      if (b && !seen[b]) { seen[b] = 1; lines.push(b); }
    }
    for (i = 0; i < loose.length; i++) {
      var bl = sweepBody(loose[i].desc, loose[i].time);
      if (bl && !seen[bl]) { seen[bl] = 1; lines.push(bl); }
    }

    if (datesE.length) {
      var merged = [];
      for (i = 0; i < datesE.length; i++) {
        var nc = nextClusterDates(datesE[i].code, now);
        if (nc) merged = merged.concat(nc);
      }
      var mk = {}, uniq = [];
      for (i = 0; i < merged.length; i++) {
        var dk = dayKey(merged[i].y, merged[i].m, merged[i].d);
        if (!mk[dk]) { mk[dk] = 1; uniq.push(merged[i]); }
      }
      uniq.sort(function (a, b) { return dayKey(a.y, a.m, a.d) - dayKey(b.y, b.m, b.d); });
      if (uniq.length) {
        var dl = formatDatesByMonth(uniq);
        if (!seen[dl]) lines.push(dl);
      }
    }
    return lines;
  }

  // Both sides' lines, unlabelled when identical,
  // else "<label>: <line>" with the car's side first. labels = [even, odd].
  function formatBothSides(even, odd, now, carSide, labels) {
    labels = labels || ['Even', 'Odd'];
    var ev = formatScheduleSide(even, now), od = formatScheduleSide(odd, now);
    if (ev.length && ev.join('\n') === od.join('\n')) return ev.slice();
    var groups = [[labels[0], ev], [labels[1], od]];
    if (carSide === 'odd') groups.reverse();
    var out = [];
    groups.forEach(function (g) {
      g[1].forEach(function (ln) { out.push(g[0] + ': ' + ln); });
    });
    return out;
  }

  global.BroomUrgency = {
    WEEKDAYS: WEEKDAYS,
    addDays: addDays,
    dayKey: function (p) { return dayKey(p.y, p.m, p.d); },
    weekday: function (p) { return pyWeekday(p.y, p.m, p.d); },
    WEEKDAY_CODES: WEEKDAY_CODES,
    NO_SWEEP_CODES: NO_SWEEP_CODES,
    checkDaySweeping: checkDaySweeping,
    sweepsOn: sweepsOn,
    datesInRange: datesInRange,
    nowForTimeZone: nowForTimeZone,
    isNoSweepCode: isNoSweepCode,
    sweepBody: sweepBody,
    sweepDays: sweepDays,
    timeDisplay: timeDisplay,
    formatScheduleSide: formatScheduleSide,
    formatBothSides: formatBothSides,
  };
})(typeof window !== 'undefined' ? window : globalThis);
