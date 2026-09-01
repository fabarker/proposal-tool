(function () {
'use strict';
/* =============================================================================
   Picker layer: the + column's add-comparison popover (spec 7.3).

   Controls mirror the base tier - Allocation select, the two exclusion tick
   boxes, Risk level - because the spec never enumerates the popover's own
   controls and Q32 settled that four options plus two tick boxes beat a
   compound select (recorded as gap G2). Adding happens here: "Add to table"
   inserts the column at once and the popover stays open with the risk select
   cleared, so three comparisons are three presses. Below 1040px it becomes a
   bottom sheet (spec 12).
   ========================================================================== */
App.plusColumn = true;

var pop = document.createElement('div');
pop.className = 'pop';
pop.setAttribute('role', 'dialog');
pop.setAttribute('aria-modal', 'false');
pop.setAttribute('aria-label', 'Add a comparison portfolio');
function mountPop() { document.body.appendChild(pop); }
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', mountPop);
} else { mountPop(); }

/* excludeTAA is pinned true - the popover mirrors the base tier (D12), and
   tactical allocation left the strategic step entirely (D50). */
var cur = { allocation: '', excludeRE: false, excludeTAA: true, riskLevel: '' };
var opener = null;
var justAdded = '';

function currentKey() {
  if (!cur.allocation || !cur.riskLevel) return null;
  var canRE = App.reAllowed(cur.allocation);
  return {
    allocation: cur.allocation,
    excludeRE: canRE ? cur.excludeRE : true,
    excludeTAA: cur.excludeTAA,
    riskLevel: cur.riskLevel
  };
}

function paint() {
  var left = App.slotsLeft();
  if (left <= 0) {
    pop.innerHTML = '<button type="button" class="pop-close" aria-label="Close">×</button>'
      + '<h4>Add comparison</h4>'
      + '<p class="pop-full">All three comparison slots are in use. Remove one from the rail '
      + 'to free a slot.</p>'
      + '<div class="pop-actions"><button type="button" class="btn btn-ghost" id="pdone">'
      + 'Done</button></div>';
    return;
  }
  var canRE = cur.allocation ? App.reAllowed(cur.allocation) : false;
  var key = currentKey();
  var ok = key && App.available(key) && !App.used(key);
  var allocationOptions = (cur.allocation ? '' : '<option value="" selected>Select…</option>')
    + App.opt('options.allocations', []).map(function (a) {
        return '<option' + (a === cur.allocation ? ' selected' : '') + '>' + App.esc(a) + '</option>';
      }).join('');
  var riskOptions = '<option value=""' + (cur.riskLevel ? '' : ' selected') + '>Select…</option>'
    + App.opt('options.riskLevels', []).map(function (r) {
        var probe = cur.allocation ? {
          allocation: cur.allocation,
          excludeRE: canRE ? cur.excludeRE : true,
          excludeTAA: cur.excludeTAA,
          riskLevel: r
        } : null;
        var why = '';
        var enabled = true;
        if (probe) {
          if (!App.available(probe)) { enabled = false; why = ' — unavailable'; }
          else if (App.used(probe)) { enabled = false; why = ' — already added'; }
        }
        return '<option value="' + App.esc(r) + '"' + (r === cur.riskLevel ? ' selected' : '')
          + (enabled ? '' : ' disabled') + '>' + App.esc(r) + why + '</option>';
      }).join('');

  pop.innerHTML = '<button type="button" class="pop-close" aria-label="Close">×</button>'
    + '<h4>Add comparison</h4>'
    + '<p class="slots">' + left + ' slot' + (left === 1 ? '' : 's') + ' remaining'
    + (justAdded ? ' · <span class="pop-added">' + App.esc(justAdded) + ' added</span>' : '')
    + '</p>'
    + '<div class="field"><label for="pa">Allocation</label><select id="pa">'
    + allocationOptions + '</select></div>'
    + '<div class="chk"><input type="checkbox" id="pre"'
    + ((cur.allocation && !canRE) || cur.excludeRE ? ' checked' : '')
    + ((cur.allocation && canRE) ? '' : ' disabled')
    + ((cur.allocation && !canRE) ? ' aria-describedby="prenote"' : '') + '>'
    + '<label for="pre">Exclude Real Estate</label></div>'
    + ((cur.allocation && !canRE)
        ? '<p class="chk-note" id="prenote">Not available — ' + App.esc(cur.allocation)
          + ' holds no real estate.</p>' : '')
    + '<div class="field"><label for="pr">Risk level</label><select id="pr"'
    + (cur.allocation ? '' : ' disabled') + '>' + riskOptions + '</select></div>'
    + '<div class="pop-actions">'
    + '<button type="button" class="btn btn-primary" id="padd"' + (ok ? '' : ' disabled') + '>'
    + 'Add to table</button>'
    + '<button type="button" class="btn btn-ghost" id="pdone">Done</button></div>';
}

function isSheet() {
  return window.matchMedia && window.matchMedia('(max-width: 1039px)').matches;
}

function open(button) {
  opener = button;
  justAdded = '';
  paint();
  pop.classList.add('on');
  pop.classList.toggle('sheet', isSheet());
  if (!isSheet()) {
    var rect = button.getBoundingClientRect();
    var height = pop.offsetHeight || 320;
    pop.style.top = Math.max(12, Math.min(window.innerHeight - height - 12, rect.bottom + 8)) + 'px';
    pop.style.left = Math.max(12, Math.min(window.innerWidth - 292, rect.right - 280)) + 'px';
  } else {
    pop.style.top = ''; pop.style.left = '';
  }
  var field = pop.querySelector('#pa');
  if (field) field.focus();
}

function close() {
  pop.classList.remove('on');
  var plus = document.getElementById('plusbtn');
  if (plus) plus.focus();
  else if (opener && opener.isConnected) opener.focus();
}

document.addEventListener('click', function (e) {
  if (e.target.id === 'plusbtn') { open(e.target); return; }
  /* a click whose target was re-rendered away mid-bubble is not an outside
     click - without this, "Add to table" would close the popover it just
     repainted */
  if (!e.target.isConnected) return;
  if (e.target.closest && e.target.closest('.pop')) return;
  if (pop.classList.contains('on')) close();
});

pop.addEventListener('change', function (e) {
  if (e.target.id === 'pa') {
    cur.allocation = e.target.value;
    cur.excludeRE = false;
    cur.riskLevel = '';
    justAdded = '';
    paint();
    var risk = pop.querySelector('#pr');
    if (risk && cur.allocation) risk.focus();
    return;
  }
  if (e.target.id === 'pre') { cur.excludeRE = e.target.checked; paint(); return; }
  if (e.target.id === 'pr') { cur.riskLevel = e.target.value || ''; paint(); return; }
});

pop.addEventListener('click', function (e) {
  if (e.target.id === 'padd') {
    var key = currentKey();
    if (key && App.addComparison(key)) {
      justAdded = App.headerName(key);
      cur.riskLevel = '';
      paint();
      var risk = pop.querySelector('#pr');
      if (risk) risk.focus();
    }
    return;
  }
  if (e.target.id === 'pdone' || (e.target.closest && e.target.closest('.pop-close'))) close();
});

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' && pop.classList.contains('on')) close();
});

App.setPicker({
  render: function () { if (pop.classList.contains('on')) paint(); }
});
})();
