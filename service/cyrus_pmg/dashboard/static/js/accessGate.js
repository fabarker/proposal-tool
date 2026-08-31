// Access gate - mirror of the host's accessGate.js.
//
// Probes /api/whoami and paints a small identity badge, a sign-in banner on
// 401, or a blocking overlay on 403. Loaded first on every page so the
// overlay can render before anything else. Server-side enforcement is the
// Flask before_request gate; this is the visible layer on top of it.
'use strict';

(function () {
    function el(tag, css, text) {
        var node = document.createElement(tag);
        if (css) node.style.cssText = css;
        if (text) node.textContent = text;
        return node;
    }

    function badge(text, background, color) {
        var node = el('div',
            'position:fixed;top:10px;right:10px;z-index:9999;font:12px/1.4 system-ui,sans-serif;' +
            'padding:4px 10px;border-radius:999px;background:' + background + ';color:' + color +
            ';box-shadow:0 1px 4px rgba(0,0,0,.18);', text);
        node.id = 'accessGateBadge';
        return node;
    }

    function overlay(title, message) {
        var scrim = el('div',
            'position:fixed;inset:0;z-index:9998;background:rgba(16,24,40,.55);display:flex;' +
            'align-items:center;justify-content:center;font-family:system-ui,sans-serif;');
        var card = el('div',
            'background:#fff;border-radius:10px;padding:26px 32px;max-width:32rem;' +
            'box-shadow:0 8px 30px rgba(0,0,0,.25);');
        card.appendChild(el('h1', 'font-size:19px;margin:0 0 8px;color:#9b1c1c;', title));
        card.appendChild(el('p', 'margin:0;color:#1c2733;', message));
        scrim.appendChild(card);
        return scrim;
    }

    function mount(node) {
        if (document.body) { document.body.appendChild(node); return; }
        document.addEventListener('DOMContentLoaded', function () {
            document.body.appendChild(node);
        });
    }

    fetch('/api/whoami', { credentials: 'same-origin' })
        .then(function (resp) {
            return resp.json().then(function (body) { return { status: resp.status, body: body }; });
        })
        .then(function (result) {
            if (result.status === 200 && result.body && result.body.kerberos) {
                mount(badge(result.body.kerberos, '#e7f4ec', '#176a33'));
                window.accessGateUser = result.body.kerberos;
            } else if (result.status === 401 && result.body && result.body.loginUrl) {
                var b = badge('Not signed in · sign in', '#fde8e8', '#9b1c1c');
                b.style.cursor = 'pointer';
                b.addEventListener('click', function () { window.location = result.body.loginUrl; });
                mount(b);
            } else if (result.status === 403) {
                mount(overlay('Access denied',
                    (result.body && result.body.error) ||
                    'You are not on the PMG dashboard access list.'));
            }
            // Anything else (backend down, proxy 502): stay silent - the page's
            // own error handling reports it where the user is working.
        })
        .catch(function () { /* network failure: same silence */ });
})();
