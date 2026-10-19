// Sweep-code evaluation and schedule display (grammar: data/README.md). Tiles
// and /check carry raw codes; checkDaySweeping gives today/tomorrow/safe
// against a region-local "now", minus the city's holidays (`skip`, holidaySkip). Tested by tests/test_urgency.py and
// tests/test_datetime.py.
(function (global) {
  'use strict';

  // Same tables as analysis.WEEKDAY_CODES / NO_SWEEP_CODES.
  var WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  var MONTH_ABBR = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
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

  // ── Days (calendar arithmetic only; no JS Date tz pitfalls) ──────────────────
  // Days are {y, m, d}. Python weekday(): Mon=0..Sun=6; JS getUTCDay(): Sun=0.
  function pyWeekday(y, m, d) { return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7; }

  function dayKey(y, m, d) { return y * 10000 + m * 100 + d; }

  function addDays(p, n) {
    var dt = new Date(Date.UTC(p.y, p.m - 1, p.d + n));
    return { y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate() };
  }

  function diffDays(a, b) {
    return Math.round((Date.UTC(b.y, b.m - 1, b.d) - Date.UTC(a.y, a.m - 1, a.d)) / 86400000);
  }

  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  // "2026-10-14" (the /check-home pickup format).
  function isoDay(p) { return p.y + '-' + pad2(p.m) + '-' + pad2(p.d); }

  // "Wed Oct 14".
  function dayLabel(p) { return WEEKDAYS[pyWeekday(p.y, p.m, p.d)] + ' ' + MONTH_ABBR[p.m] + ' ' + p.d; }

  // ── Region-local clock ───────────────────────────────────────────────────────
  var _clockFmt = {};
  // {y, m, d, min} for the instant `when` (default now) in IANA zone `tz`
  // (undefined: the browser's zone); min = minutes since local midnight.
  function nowForTimeZone(tz, when) {
    var d = when || new Date(), parts = {};
    try {
      var fmt = _clockFmt[tz] || (_clockFmt[tz] = new Intl.DateTimeFormat('en-US', {
        timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', hour12: false,
      }));
      fmt.formatToParts(d).forEach(function (p) { parts[p.type] = p.value; });
    } catch (e) {  // unknown zone: the browser's
      parts = { year: d.getFullYear(), month: d.getMonth() + 1, day: d.getDate(),
                hour: d.getHours(), minute: d.getMinutes() };
    }
    var hour = parseInt(parts.hour, 10) % 24; // Intl may emit "24" at midnight
    return {
      y: parseInt(parts.year, 10), m: parseInt(parts.month, 10),
      d: parseInt(parts.day, 10), min: hour * 60 + parseInt(parts.minute, 10),
    };
  }

  // ── Sweep-code rules ─────────────────────────────────────────────────────────
  // Sorted, valid days of a 'DATES:' code; null for any other code.
  function parseDatesCode(code) {
    if (typeof code !== 'string' || code.trim().toUpperCase().indexOf('DATES:') !== 0) return null;
    var out = [], parts = code.trim().slice(6).split(',');
    for (var i = 0; i < parts.length; i++) {
      var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(parts[i].trim());
      var p = m && { y: +m[1], m: +m[2], d: +m[3] }, real = p && addDays(p, 0);
      if (p && real.m === p.m && real.d === p.d) out.push(p);  // drops "2026-02-30"
    }
    out.sort(function (a, b) { return dayKey(a.y, a.m, a.d) - dayKey(b.y, b.m, b.d); });
    return out;
  }

  var _ruleCache = {};
  // Weekly code: {ranks: [weekday...] ascending, weekdays: {rank: 1},
  // ordinals: {n: 1} | null (every week)}. 'DATES:' code: {dates: [day...],
  // keys: {dayKey: 1}}. null for no-sweep and unreadable codes.
  function codeRule(code) {
    if (typeof code !== 'string') return null;
    if (Object.prototype.hasOwnProperty.call(_ruleCache, code)) return _ruleCache[code];
    var rule = null, i, dates = parseDatesCode(code);
    if (dates !== null) {
      var keys = {};
      for (i = 0; i < dates.length; i++) keys[dayKey(dates[i].y, dates[i].m, dates[i].d)] = 1;
      rule = { dates: dates, keys: keys };
    } else {
      var c = code.trim().toUpperCase(), m = !NO_SWEEP[c] && CODE_RE.exec(c);
      if (m && (m[1] || m[2] === 'E')) {
        var wds = {}, toks = m[1].match(DAY_TOKEN_RE) || [];
        for (i = 0; i < toks.length; i++) wds[WEEKDAY_CODES[toks[i]][0]] = 1;
        if (!toks.length) for (i = 0; i < 7; i++) wds[i] = 1;  // "E": every day
        var ords = null;
        if (m[2] && m[2] !== 'E') {
          ords = {};
          for (i = 0; i < m[2].length; i++) ords[+m[2].charAt(i)] = 1;
        }
        rule = { weekdays: wds, ordinals: ords,
                 ranks: Object.keys(wds).map(Number).sort(function (a, b) { return a - b; }) };
      }
    }
    _ruleCache[code] = rule;
    return rule;
  }

  // ── Holidays ─────────────────────────────────────────────────────────────────
  // A city's holidays as /api/cities serves them ({iso: {name, suspends}}) -> the days
  // `service` ('sweeping', 'trash') is suspended: {dayKey: holiday name}. The functions
  // below take this as an optional `skip` (no sweeping on those days).
  var _skipCache = typeof WeakMap === 'function' ? new WeakMap() : null;
  function holidaySkip(cityHolidays, service) {
    if (!cityHolidays) return null;
    var byService = _skipCache && _skipCache.get(cityHolidays);
    if (byService && byService[service]) return byService[service];
    var out = {};
    Object.keys(cityHolidays).forEach(function (iso) {
      var h = cityHolidays[iso], m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
      if (m && h && (h.suspends || []).indexOf(service) >= 0) out[dayKey(+m[1], +m[2], +m[3])] = h.name;
    });
    if (_skipCache) { byService = byService || {}; byService[service] = out; _skipCache.set(cityHolidays, byService); }
    return out;
  }
  // The holiday's name when `skip` suspends day p, else ''.
  function holidayOn(skip, p) { return (skip && skip[dayKey(p.y, p.m, p.d)]) || ''; }

  function sweepsOn(code, y, m, d, skip) {
    if (skip && skip[dayKey(y, m, d)]) return false;
    var r = codeRule(code);
    if (!r) return false;
    if (r.dates) return r.keys[dayKey(y, m, d)] === 1;
    if (!r.weekdays[pyWeekday(y, m, d)]) return false;
    return !r.ordinals || r.ordinals[Math.floor((d - 1) / 7) + 1] === 1;
  }

  // Sweep dates of `code` in [start, end] inclusive ({y,m,d} objects).
  function datesInRange(code, start, end, skip) {
    var out = [], cur = start, endK = dayKey(end.y, end.m, end.d);
    while (dayKey(cur.y, cur.m, cur.d) <= endK) {
      if (sweepsOn(code, cur.y, cur.m, cur.d, skip)) out.push(cur);
      cur = addDays(cur, 1);
    }
    return out;
  }

  // Both sides' sweeps in [start, end]:
  // [{y, m, d, items: [{side: 'even'|'odd', time}]}] date-sorted.
  function sweepDays(even, odd, start, end, skip) {
    var byKey = {};
    [['even', even || []], ['odd', odd || []]].forEach(function (so) {
      so[1].forEach(function (e) {
        var time = (typeof e[2] === 'string' && e[2].trim()) ? e[2] : '';
        datesInRange(e[0], start, end, skip).forEach(function (d) {
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

  // ── Time windows ─────────────────────────────────────────────────────────────
  // Lenient (same pattern as normalize._TIME_RANGE_RE): tolerates the PDF
  // artifacts in raw tile data — bullet/middot/"o" separators, stray spaces
  // inside hours/minutes and around colons, "A M"/"P,M".
  var TIME_RANGE_RE = new RegExp(
    '(\\d\\s*\\d?)\\s*(?::\\s*(\\d\\s*\\d))?\\s*(A[\\s,]*M|P[\\s,]*M)' +
    '[\\s,]*(?:[-–—•·o]|to)\\s*' +
    '(\\d\\s*\\d?)\\s*(?::\\s*(\\d\\s*\\d))?\\s*(A[\\s,]*M|P[\\s,]*M)', 'i');
  // A bare "AM" / "PM" (Berkeley) is a morning / afternoon route.
  var HALF_DAY = { AM: [0, 720], PM: [720, 1440] };

  // [hour 1–12, minute, 'AM'|'PM'] minus stray spaces, or null when not a clock time.
  function _clock(h, mn, ap) {
    var hh = parseInt(h.replace(/\s+/g, ''), 10), mm = mn ? parseInt(mn.replace(/\s+/g, ''), 10) : 0;
    if (hh < 1 || hh > 12 || mm > 59) return null;
    return [hh, mm, ap.replace(/[\s,]+/g, '').toUpperCase()];
  }
  function _minutes(c) { return (c[0] % 12 + (c[2] === 'PM' ? 12 : 0)) * 60 + c[1]; }
  function _fmtClock(c) { return c[0] + (c[1] ? ':' + pad2(c[1]) : '') + c[2]; }

  // [start, end] clocks of a "8:00AM-10:00AM"-style window, or null.
  function _range(raw) {
    var m = typeof raw === 'string' ? TIME_RANGE_RE.exec(raw) : null;
    if (!m) return null;
    var a = _clock(m[1], m[2], m[3]), b = _clock(m[4], m[5], m[6]);
    return a && b ? [a, b] : null;
  }

  // [start, end] minutes since midnight, or null when unreadable. A window
  // ending at 12AM ends at 1440; end < start runs past midnight.
  function timeWindow(raw) {
    var r = _range(raw);
    if (r) return [_minutes(r[0]), _minutes(r[1]) || 1440];
    var half = typeof raw === 'string' && HALF_DAY[raw.trim().toUpperCase()];
    return half ? half.slice() : null;
  }

  // Compact window "8AM–9:30AM"; 'N/A' when missing; anything unreadable as is.
  function timeDisplay(raw) {
    if (typeof raw !== 'string') return 'N/A';
    var s = raw.trim();
    if (s === '' || /^(n\/a|na|none|nan)$/i.test(s)) return 'N/A';
    var r = _range(s);
    if (r) return _fmtClock(r[0]) + '–' + _fmtClock(r[1]);
    return HALF_DAY[s.toUpperCase()] ? s.toUpperCase() : s;
  }

  // ── Urgency verdict ──────────────────────────────────────────────────────────
  // A sweep starting this soon is today's problem even when it falls after
  // midnight: a 12AM window means moving the car tonight, not tomorrow.
  var LEAD_MIN = 6 * 60;

  // entries: [{code, time}, ...]; now: {y, m, d, min} (min = minutes since
  // region-local midnight). 'today' while a window today is still to come or
  // running (all day when untimed), while yesterday's runs past midnight, or
  // when tomorrow's starts within LEAD_MIN (untimed: at midnight); 'tomorrow'
  // when one is tomorrow; else 'safe'.
  function checkDaySweeping(entries, now, skip) {
    var yst = addDays(now, -1), tmr = addDays(now, 1), tomorrow = false;
    for (var i = 0; i < entries.length; i++) {
      var code = entries[i].code, w = timeWindow(entries[i].time || '');
      var overnight = w && w[1] < w[0];
      if (sweepsOn(code, now.y, now.m, now.d, skip) && (!w || overnight || now.min <= w[1])) return 'today';
      if (overnight && now.min <= w[1] && sweepsOn(code, yst.y, yst.m, yst.d, skip)) return 'today';
      if (sweepsOn(code, tmr.y, tmr.m, tmr.d, skip)) {
        if (1440 - now.min + (w ? w[0] : 0) <= LEAD_MIN) return 'today';
        tomorrow = true;
      }
    }
    return tomorrow ? 'tomorrow' : 'safe';
  }

  // "9AM", "9:30AM", "12PM"; 0 or 1440 is "midnight".
  function clockText(min) {
    var m = min % 1440;
    if (m === 0) return 'midnight';
    var h = Math.floor(m / 60), mm = m % 60;
    return (h % 12 || 12) + (mm ? ':' + pad2(mm) : '') + (h < 12 ? 'AM' : 'PM');
  }

  // When the car must be gone, by the same rules as checkDaySweeping: the most pressing
  // of {state: 'running', at: end} (a window is on now), {state: 'by', at: start} (one
  // starts later today, or within LEAD_MIN tomorrow), {state: 'today'} (untimed today),
  // {state: 'tomorrow', at: start | null}; null when nothing is due. `at` is a clockText.
  function sweepDeadline(entries, now, skip) {
    var yst = addDays(now, -1), tmr = addDays(now, 1), best = null;
    var rank = { running: 0, by: 1, today: 2, tomorrow: 3 };
    function take(state, at, soon) {
      if (!best || rank[state] < rank[best.state] || (rank[state] === rank[best.state] && soon < best.soon)) {
        best = { state: state, at: at, soon: soon };
      }
    }
    for (var i = 0; i < entries.length; i++) {
      var code = entries[i].code, w = timeWindow(entries[i].time || '');
      var overnight = w && w[1] < w[0];
      if (sweepsOn(code, now.y, now.m, now.d, skip)) {
        if (!w) take('today', null, 0);
        else if (now.min < w[0]) take('by', w[0], w[0] - now.min);
        else if (overnight || now.min <= w[1]) take('running', w[1], 0);
      }
      if (overnight && now.min <= w[1] && sweepsOn(code, yst.y, yst.m, yst.d, skip)) take('running', w[1], 0);
      if (sweepsOn(code, tmr.y, tmr.m, tmr.d, skip)) {
        var start = w ? w[0] : 0, lead = 1440 - now.min + start;
        if (lead <= LEAD_MIN) take('by', start, lead);
        else take('tomorrow', start || null, lead);
      }
    }
    return best && { state: best.state, at: best.at === null ? null : clockText(best.at) };
  }

  // ── Schedule display (card, hover and center banner) ────────────────────────
  // "Apr 17, 18; May 15" (dates sorted).
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

  // The next run of dates from today on (a zone's sides are swept a few days apart).
  function nextCluster(dates, now, maxGap, maxDates) {
    var todayK = dayKey(now.y, now.m, now.d), out = [];
    for (var i = 0; i < dates.length && out.length < maxDates; i++) {
      var d = dates[i];
      if (dayKey(d.y, d.m, d.d) < todayK) continue;
      if (out.length && diffDays(out[out.length - 1], d) > maxGap) break;
      out.push(d);
    }
    return out;
  }

  // Bare week-of-month ordinals "13" -> "1st & 3rd".
  function _prettyOrdinals(run) {
    var parts = run.split('').map(function (d) {
      return d + (d === '1' ? 'st' : d === '2' ? 'nd' : d === '3' ? 'rd' : 'th');
    });
    return parts.length === 1 ? parts[0]
         : parts.slice(0, -1).join(', ') + ' & ' + parts[parts.length - 1];
  }

  function hasAllFour(ords) {
    return ords[1] && ords[2] && ords[3] && ords[4];
  }

  // Sorted weekday ranks -> "Mon, Wed, Fri"; runs of 3+ become "Mon–Fri".
  function dayList(ranks) {
    var parts = [];
    for (var i = 0; i < ranks.length; ) {
      var j = i;
      while (j + 1 < ranks.length && ranks[j + 1] === ranks[j] + 1) j++;
      if (j - i >= 2) parts.push(WEEKDAYS[ranks[i]] + '–' + WEEKDAYS[ranks[j]]);
      else for (var k = i; k <= j; k++) parts.push(WEEKDAYS[ranks[k]]);
      i = j + 1;
    }
    return parts.join(', ');
  }

  // Between what days and when: "Tue, Thu · 3AM–5AM" (t is a timeDisplay).
  var SEP = ' · ';
  function withTime(label, t) {
    return (t && t !== 'N/A' && label.indexOf(t) === -1) ? (label + SEP + t) : label;
  }

  function _ent(e) {
    if (Array.isArray(e)) return { code: e[0], desc: e[1] || '', time: e[2] || '' };
    return { code: e.code, desc: e.desc || '', time: e.time || '' };
  }

  // entries: array of {code,desc,time} or [code,desc,time]. Returns display lines.
  function formatScheduleSide(entries, now) {
    var perDay = {}, perDayOrder = [], loose = [], dated = [], i;
    for (i = 0; i < (entries || []).length; i++) {
      var e = _ent(entries[i]), r = codeRule(e.code);
      if (r && r.dates) { dated = dated.concat(r.dates); continue; }
      if (!r) {
        if (typeof e.code === 'string' && !isNoSweepCode(e.code)) loose.push(e);
        continue;
      }
      // Per (weekday, time): which weeks of the month (every: all of them).
      var t = timeDisplay(e.time);
      for (var j = 0; j < r.ranks.length; j++) {
        var dk = r.ranks[j] + '|' + t;
        if (!perDay[dk]) { perDay[dk] = { rank: r.ranks[j], time: t, every: false, ords: {} }; perDayOrder.push(dk); }
        if (!r.ordinals) perDay[dk].every = true;
        else for (var o in r.ordinals) perDay[dk].ords[o] = 1;
      }
    }
    // Days sharing the same weeks and time collapse into one line.
    var slots = {}, slotOrder = [];
    for (i = 0; i < perDayOrder.length; i++) {
      var pd = perDay[perDayOrder[i]];
      var weeks = pd.every || hasAllFour(pd.ords) ? '' : Object.keys(pd.ords).sort().join('');
      var sk = weeks + '|' + pd.time;
      if (!slots[sk]) { slots[sk] = { weeks: weeks, time: pd.time, ranks: [] }; slotOrder.push(sk); }
      slots[sk].ranks.push(pd.rank);
    }
    var ranked = [];
    for (i = 0; i < slotOrder.length; i++) {
      var sl = slots[slotOrder[i]];
      sl.ranks.sort(function (a, b) { return a - b; });
      var label = sl.ranks.length === 7 && !sl.weeks ? 'Every day' : dayList(sl.ranks);
      if (sl.weeks) label += ' ' + _prettyOrdinals(sl.weeks);
      ranked.push([sl.ranks[0], sl.weeks ? 1 : 0, withTime(label, sl.time)]);
    }
    ranked.sort(function (a, b) { return (a[0] - b[0]) || (a[1] - b[1]); });

    var lines = ranked.map(function (x) { return x[2]; });
    // Unreadable codes: the source description, as published.
    for (i = 0; i < loose.length; i++) {
      var d = loose[i].desc.trim();
      if (d && d.toUpperCase() !== 'N/A') lines.push(withTime(d, timeDisplay(loose[i].time)));
    }
    if (dated.length) {
      var uniq = {}, sorted = [];
      dated.forEach(function (p) {
        var k = dayKey(p.y, p.m, p.d);
        if (!uniq[k]) { uniq[k] = 1; sorted.push(p); }
      });
      sorted.sort(function (a, b) { return dayKey(a.y, a.m, a.d) - dayKey(b.y, b.m, b.d); });
      var next = nextCluster(sorted, now, 4, 3);
      if (next.length) lines.push(formatDatesByMonth(next));
    }
    return lines.filter(function (ln, k) { return lines.indexOf(ln) === k; });
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

  // One calendar day's sweeps (sweepDays items) as text: "9AM–12PM" when both
  // sides share the times, "Even side 9AM–12PM" for one side, else per side.
  function dayText(items, labels, oneSide) {
    labels = labels || ['Even', 'Odd'];
    var per = { even: [], odd: [] };
    items.forEach(function (it) {
      var t = it.time ? timeDisplay(it.time) : 'time n/a';
      if (per[it.side].indexOf(t) < 0) per[it.side].push(t);
    });
    if (oneSide) return per.even.concat(per.odd).join(', ');  // the side is known (a car's)
    if (!per.odd.length) return labels[0] + ' side ' + per.even.join(', ');
    if (!per.even.length) return labels[1] + ' side ' + per.odd.join(', ');
    if (per.even.join() === per.odd.join()) return per.even.join(', ');
    return [labels[0] + ' ' + per.even.join(', '), labels[1] + ' ' + per.odd.join(', ')].join(', ');
  }

  global.BroomUrgency = {
    WEEKDAYS: WEEKDAYS,
    addDays: addDays,
    dayKey: function (p) { return dayKey(p.y, p.m, p.d); },
    weekday: function (p) { return pyWeekday(p.y, p.m, p.d); },
    isoDay: isoDay,
    dayLabel: dayLabel,
    WEEKDAY_CODES: WEEKDAY_CODES,
    NO_SWEEP_CODES: NO_SWEEP_CODES,
    checkDaySweeping: checkDaySweeping,
    sweepDeadline: sweepDeadline,
    clockText: clockText,
    sweepsOn: sweepsOn,
    holidaySkip: holidaySkip,
    holidayOn: holidayOn,
    datesInRange: datesInRange,
    nowForTimeZone: nowForTimeZone,
    isNoSweepCode: isNoSweepCode,
    sweepDays: sweepDays,
    timeDisplay: timeDisplay,
    timeWindow: timeWindow,
    dayText: dayText,
    formatScheduleSide: formatScheduleSide,
    formatBothSides: formatBothSides,
  };
})(typeof window !== 'undefined' ? window : globalThis);
