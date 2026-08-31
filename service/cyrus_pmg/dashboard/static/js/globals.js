// Shared page globals - mirror of the host's globals.js.
// Sets window.API_BASE for every page; pages still guard with
// `if (typeof API_BASE === 'undefined')` so they work standalone.
'use strict';

window.API_BASE = window.location.origin + '/api';
