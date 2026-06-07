// Client-side port of broombuster.analysis urgency logic. Tiles carry raw
// schedule codes (sched property); this computes today/tomorrow/clear against a
// region-local "now" so colour matches the server. Must stay behaviour-identical
// to analysis.compute_urgency / parse_sweeping_code (tests/test_urgency_parity.py).
(function (global) {
  'use strict';

  // Mirror analysis.py tables.
  var WEEKDAY_MAP = { M: 0, T: 1, W: 2, TH: 3, F: 4, S: 5, SU: 6 };
  var COMPOUND_MAP = {
    MWF: [0, 2, 4], TTH: [1, 3], TTHS: [1, 3, 5], MF: [0, 4],
    TF: [1, 4], THF: [3, 4], E: [0, 1, 2, 3, 4, 5, 6],
  };
  var ORDINALS = { '1': [1], '2': [2], '3': [3], '4': [4], '13': [1, 3], '24': [2, 4] };
  var NO_SWEEP = {
    N: 1, NS: 1, O: 1, 'N-S': 1, 'N-E': 1, 'N-O': 1,
    'NS-UC': 1, 'NS-H': 1, 'NS-O': 1, 'NS-A': 1,
  };
  var TIME_RANGE_RE =
    /(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\s*(?:[-–—]|to)\s*(\d{1,2})(?::(\d{2}))?\s*(AM|PM)/i;

  function isNoSweepCode(code) {
    return typeof code === 'string' && NO_SWEEP[code.trim().toUpperCase()] === 1;
  }

  // ── Date helpers (calendar arithmetic only; no JS Date tz pitfalls) ──────────
  function daysInMonth(y, m) { return new Date(Date.UTC(y, m, 0)).getUTCDate(); }

  // Python weekday(): Mon=0..Sun=6. JS getUTCDay(): Sun=0..Sat=6.
  function pyWeekday(y, m, d) { return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7; }

  function dayKey(y, m, d) { return y * 10000 + m * 100 + d; }

  function addOneDay(y, m, d) {
    var dt = new Date(Date.UTC(y, m - 1, d + 1));
    return { y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate() };
  }

  function getAllDatesForWeekday(weekday, year, month) {
    var out = [];
    var n = daysInMonth(year, month);
    for (var day = 1; day <= n; day++) {
      if (pyWeekday(year, month, day) === weekday) out.push({ y: year, m: month, d: day });
    }
    return out;
  }

  function getWeekdaysByOrdinal(weekday, ordinalList, year, month) {
    var dates = getAllDatesForWeekday(weekday, year, month);
    var out = [];
    for (var i = 0; i < ordinalList.length; i++) {
      var idx = ordinalList[i];
      if (idx <= dates.length) out.push(dates[idx - 1]);
    }
    return out;
  }

  // ── Time parsing (mirror _parse_time_range; returns end minutes-of-day) ──────
  function parseEndMinutes(timeStr) {
    if (typeof timeStr !== 'string' || timeStr.trim() === '') return null;
    var m = TIME_RANGE_RE.exec(timeStr);
    if (!m) return null;
    var h = parseInt(m[4], 10);
    var mn = parseInt(m[5] || '0', 10);
    var ap = m[6].toUpperCase();
    if (ap === 'PM' && h !== 12) h += 12;
    else if (ap === 'AM' && h === 12) h = 0;
    return h * 60 + mn;
  }

  // ── Sweep-code expansion (mirror _parse_sweeping_code_cached) ────────────────
  var _expandCache = {};
  function expandCode(code, year, month) {
    var key = code + '|' + year + '|' + month;
    if (_expandCache[key]) return _expandCache[key];
    var out = _expand(code.toUpperCase(), year, month);
    _expandCache[key] = out;
    return out;
  }

  function _expand(code, year, month) {
    var i, dd;
    if (COMPOUND_MAP[code]) {
      var res = [];
      var wds = COMPOUND_MAP[code];
      for (i = 0; i < wds.length; i++) res = res.concat(getAllDatesForWeekday(wds[i], year, month));
      return res;
    }
    if (code.charAt(code.length - 1) === 'E') {
      var dayCode = code.slice(0, -1);
      var wd = WEEKDAY_MAP[dayCode];
      if (wd !== undefined) return getAllDatesForWeekday(wd, year, month);
      var cwds = COMPOUND_MAP[dayCode];
      if (cwds !== undefined) {
        var r2 = [];
        for (i = 0; i < cwds.length; i++) r2 = r2.concat(getAllDatesForWeekday(cwds[i], year, month));
        return r2;
      }
    }
    if (code === 'S' || code === 'SU') {
      return getAllDatesForWeekday(WEEKDAY_MAP[code], year, month);
    }
    for (var suffix in ORDINALS) {
      if (Object.prototype.hasOwnProperty.call(ORDINALS, suffix)
          && code.length > suffix.length
          && code.slice(code.length - suffix.length) === suffix) {
        var dc = code.slice(0, code.length - suffix.length);
        var w2 = WEEKDAY_MAP[dc];
        if (w2 !== undefined) return getWeekdaysByOrdinal(w2, ORDINALS[suffix], year, month);
      }
    }
    if (code === 'E') {
      var out = [];
      var n = daysInMonth(year, month);
      for (dd = 1; dd <= n; dd++) out.push({ y: year, m: month, d: dd });
      return out;
    }
    return [];
  }

  // Mirror parse_sweeping_code: current month, plus next month when tomorrow
  // (relative to `now`) crosses a month boundary.
  function parseSweepingCode(code, now) {
    if (code.toUpperCase().indexOf('DATES:') === 0) {
      var out = [];
      var parts = code.slice(6).split(',');
      for (var i = 0; i < parts.length; i++) {
        var s = parts[i].trim();
        if (!s) continue;
        var mm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
        if (mm) out.push({ y: +mm[1], m: +mm[2], d: +mm[3] });
      }
      return out;
    }
    var dates = expandCode(code, now.y, now.m).slice();
    var tomorrow = addOneDay(now.y, now.m, now.d);
    if (tomorrow.m !== now.m) dates = dates.concat(expandCode(code, tomorrow.y, tomorrow.m));
    return dates;
  }

  // ── Urgency verdict (mirror check_day_street_sweeping) ───────────────────────
  // entries: [{code, time}, ...]; now: {y, m, d, min} (min = minutes since
  // region-local midnight). Returns 'today' | 'tomorrow' | 'clear'.
  function checkDaySweeping(entries, now) {
    var sweptKeys = {};       // dayKey -> true
    var dateTimes = {};       // dayKey -> [endMinutes|null, ...]
    for (var i = 0; i < entries.length; i++) {
      var code = entries[i].code;
      if (typeof code !== 'string' || isNoSweepCode(code)) continue;
      var timeStr = entries[i].time || '';
      var dates = parseSweepingCode(code, now);
      for (var j = 0; j < dates.length; j++) {
        var k = dayKey(dates[j].y, dates[j].m, dates[j].d);
        sweptKeys[k] = true;
        if (!dateTimes[k]) dateTimes[k] = [];
        dateTimes[k].push(timeStr === '' ? null : parseEndMinutes(timeStr));
      }
    }

    var todayKey = dayKey(now.y, now.m, now.d);
    var tmr = addOneDay(now.y, now.m, now.d);
    var tomorrowKey = dayKey(tmr.y, tmr.m, tmr.d);

    if (sweptKeys[todayKey]) {
      var times = dateTimes[todayKey] || [];
      if (times.length === 0) return 'today';
      for (var t = 0; t < times.length; t++) {
        // null end = untimed swept side (still active all day) or unparseable.
        if (times[t] === null || now.min <= times[t]) return 'today';
      }
      // every timed side has closed and no untimed side — fall through.
    }
    if (sweptKeys[tomorrowKey]) return 'tomorrow';
    return 'clear';
  }

  // sched: JSON string (tile property) of [{code,time,side}, ...].
  function urgencyForSched(schedJson, now) {
    var entries;
    try { entries = JSON.parse(schedJson || '[]'); }
    catch (e) { return 'clear'; }
    if (!entries || !entries.length) return 'clear';
    return checkDaySweeping(entries, now);
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

  // ── Chicago DATES: next sweep cluster (mirror analysis.next_dates_desc) ───────
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
    maxGap = maxGap || 4;  // mirror analysis._CLUSTER_GAP_DAYS
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

  // Next upcoming cluster for a DATES code; null for non-DATES, '' when none remain.
  function nextDatesDesc(code, now, maxDates) {
    var cl = nextClusterDates(code, now, maxDates);
    if (cl === null) return null;
    if (!cl.length) return '';
    return formatDatesByMonth(cl);
  }

  // ── Canonical schedule display (mirror normalize.sweep_body + ───────────────
  //    analysis.format_schedule_side). Keeps card/hover identical to the server.
  var TIME_RANGE_RE_G =
    /(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\s*(?:[-–—]|to)\s*(\d{1,2})(?::(\d{2}))?\s*(AM|PM)/ig;
  var WEEKDAY_CANON = {
    MON: [0, 'Mon'], MONDAY: [0, 'Mon'],
    TUE: [1, 'Tue'], TUES: [1, 'Tue'], TUESDAY: [1, 'Tue'],
    WED: [2, 'Wed'], WEDS: [2, 'Wed'], WEDNESDAY: [2, 'Wed'],
    THU: [3, 'Thu'], THUR: [3, 'Thu'], THURS: [3, 'Thu'], THURSDAY: [3, 'Thu'],
    FRI: [4, 'Fri'], FRIDAY: [4, 'Fri'],
    SAT: [5, 'Sat'], SATURDAY: [5, 'Sat'],
    SUN: [6, 'Sun'], SUNDAY: [6, 'Sun'],
  };
  var CODE_WEEKDAY = [
    ['TH', [3, 'Thu']], ['SU', [6, 'Sun']], ['M', [0, 'Mon']], ['T', [1, 'Tue']],
    ['W', [2, 'Wed']], ['F', [4, 'Fri']], ['S', [5, 'Sat']],
  ];

  function timeDisplay(raw) {
    if (typeof raw !== 'string') return 'N/A';
    var s = raw.trim();
    if (s === '' || /^(n\/a|none|nan)$/i.test(s)) return 'N/A';
    TIME_RANGE_RE.lastIndex = 0;
    var m = TIME_RANGE_RE.exec(s);
    if (!m) return s;
    function fp(h, mn, ap) {
      h = parseInt(h, 10); mn = parseInt(mn || '0', 10); ap = ap.toUpperCase();
      return mn ? (h + ':' + (mn < 10 ? '0' + mn : mn) + ap) : (h + ap);
    }
    return fp(m[1], m[2], m[3]) + '–' + fp(m[4], m[5], m[6]);
  }

  function weekdayFirst(desc) {
    var toks = desc.split(' ').filter(Boolean);
    if (!toks.length) return desc;
    var every = toks[0].toLowerCase() === 'every';
    var body = every ? toks.slice(1) : toks;
    var wi = -1;
    for (var i = 0; i < body.length; i++) {
      if (WEEKDAY_CANON[body[i].replace(/[.,]/g, '').toUpperCase()]) { wi = i; break; }
    }
    if (wi < 0) return desc;
    var disp = WEEKDAY_CANON[body[wi].replace(/[.,]/g, '').toUpperCase()][1];
    var rest = body.slice(0, wi).concat(body.slice(wi + 1));
    return (every ? ['Every'] : []).concat([disp]).concat(rest).join(' ').trim();
  }

  function sweepBody(desc, time) {
    var d = (typeof desc === 'string') ? desc : '';
    d = d.replace(/\s*\(every\)/ig, '');
    d = d.replace(TIME_RANGE_RE_G, '');
    d = d.replace(/\s*\bof\s+(?:the\s+)?month\b/ig, '');
    d = d.replace(/\band\b/ig, '&');
    d = d.replace(/\s+/g, ' ').replace(/^[\s,]+|[\s,]+$/g, '').trim();
    d = weekdayFirst(d);
    if (!d || d.toUpperCase() === 'N/A') return '';
    var t = timeDisplay(time || '');
    if (t === '' || t === 'N/A' || d.indexOf(t) !== -1) return d;
    return d + ', ' + t;
  }

  function codeWeekday(code) {
    var c = code.trim().toUpperCase();
    for (var i = 0; i < CODE_WEEKDAY.length; i++) {
      if (c.indexOf(CODE_WEEKDAY[i][0]) === 0) return CODE_WEEKDAY[i][1];
    }
    return null;
  }

  function codeOrdinals(code) {
    var m = /(\d+)$/.exec(code.trim());
    var s = {};
    if (m) for (var i = 0; i < m[1].length; i++) s[+m[1].charAt(i)] = 1;
    return s;
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

    var ranked = [];
    for (i = 0; i < order.length; i++) {
      var g = groups[order[i]];
      if (g.ords[1] && g.ords[2] && g.ords[3] && g.ords[4]) {
        ranked.push([g.rank, -1, sweepBody('Every ' + g.disp, g.time)]);
      } else {
        for (var j = 0; j < g.items.length; j++) ranked.push([g.rank, 0, sweepBody(g.items[j].desc, g.items[j].time)]);
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

  global.BroomUrgency = {
    urgencyForSched: urgencyForSched,
    checkDaySweeping: checkDaySweeping,
    parseSweepingCode: parseSweepingCode,
    nextDatesDesc: nextDatesDesc,
    nextClusterDates: nextClusterDates,
    nowForTimeZone: nowForTimeZone,
    isNoSweepCode: isNoSweepCode,
    sweepBody: sweepBody,
    timeDisplay: timeDisplay,
    formatScheduleSide: formatScheduleSide,
  };
})(typeof window !== 'undefined' ? window : globalThis);
