/*
  MyScribe - the theme switch, and the only script that is not app.js.

  It is separate, and loaded synchronously in <head>, for one reason: the
  chosen theme has to be on <html> before the first paint. app.js is deferred,
  which runs it after the document is parsed - too late to promise there is no
  flash of the other theme. A blocking script in the head is the price of that
  promise; this file is kept tiny so the price stays small. It is still a file
  and not an inline <script>, so `script-src 'self'` remains possible.

  Three states, stored under one key:

      "light" / "dark"   ->  data-theme on <html>, which wins over the media
                             query in app.css
      "system" (or no stored value, or storage unreadable)
                         ->  no attribute, and `color-scheme: light dark`
                             lets the operating system decide

  Storage can throw rather than return null - a browser set to block site data
  raises on the property access itself - so every read and write is guarded and
  the failure mode is "system", which is what the page did before this existed.
*/
(function () {
  'use strict';

  var KEY = 'myscribe-theme';
  var CHOICES = ['system', 'light', 'dark'];

  function stored() {
    try {
      var value = window.localStorage.getItem(KEY);
      return CHOICES.indexOf(value) > 0 ? value : 'system';
    } catch (err) {
      return 'system';
    }
  }

  function apply(choice) {
    var root = document.documentElement;
    if (choice === 'system') {
      root.removeAttribute('data-theme');
    } else {
      root.setAttribute('data-theme', choice);
    }
  }

  /* Before anything is painted. The buttons do not exist yet - <head> runs
     ahead of the body - so marking them is a second pass, below. */
  apply(stored());

  function mark() {
    var current = stored();
    var buttons = document.querySelectorAll('.theme-switch button[data-theme-choice]');
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute('aria-pressed', String(buttons[i].dataset.themeChoice === current));
    }
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest ? event.target.closest('[data-theme-choice]') : null;
    if (!button) { return; }
    var choice = button.dataset.themeChoice;
    if (CHOICES.indexOf(choice) < 0) { return; }
    apply(choice);
    try {
      if (choice === 'system') { window.localStorage.removeItem(KEY); }
      else { window.localStorage.setItem(KEY, choice); }
    } catch (err) { /* the attribute is still set; only the memory is lost */ }
    mark();
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mark);
  } else {
    mark();
  }
}());
