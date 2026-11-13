/* Suggest verified collaborators while composing a shared message. */
((root) => {
  'use strict';
  function create(input, contacts) {
    const menu = document.createElement('div');
    menu.className = 'mention-menu'; menu.hidden = true;
    menu.setAttribute('role', 'listbox'); menu.setAttribute('aria-label', 'Collaborators');
    input.parentElement.append(menu);
    let matches = [], start = 0, selected = 0;
    function close() { menu.hidden = true; input.removeAttribute('aria-activedescendant'); }
    function update() {
      const before = input.value.slice(0, input.selectionStart);
      const fragment = before.match(/(?:^|\s)@([^\s@]*)$/);
      if (!fragment || input.selectionStart !== input.selectionEnd) return close();
      start = input.selectionStart - fragment[1].length - 1;
      matches = contacts().filter(item => item.email.toLowerCase().includes(fragment[1].toLowerCase())).slice(0, 8);
      if (!matches.length) return close();
      selected = 0; render();
    }
    function render() {
      menu.replaceChildren(...matches.map((item, index) => {
        const button = document.createElement('button'); button.type = 'button';
        button.id = `mention-option-${index}`; button.setAttribute('role', 'option');
        button.setAttribute('aria-selected', String(index === selected));
        button.textContent = item.email; button.addEventListener('mousedown', event => event.preventDefault());
        button.addEventListener('click', () => choose(index)); return button;
      }));
      menu.hidden = false; input.setAttribute('aria-activedescendant', `mention-option-${selected}`);
    }
    function choose(index) {
      const fragment = input.value.slice(start, input.selectionStart);
      if (!/^@[^\s@]*$/.test(fragment) || input.selectionStart !== input.selectionEnd) return close();
      input.setRangeText(`@${matches[index].email} `, start, input.selectionStart, 'end');
      close(); input.focus(); input.dispatchEvent(new Event('input', {bubbles:true}));
    }
    input.addEventListener('input', update);
    input.addEventListener('click', update);
    input.addEventListener('keyup', event => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) update(); });
    input.addEventListener('blur', () => setTimeout(close, 100));
    input.addEventListener('keydown', event => {
      if (menu.hidden || event.isComposing) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); close(); }
      else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault(); event.stopImmediatePropagation();
        selected = (selected + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length; render();
      } else if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault(); event.stopImmediatePropagation(); choose(selected);
      }
    }, true);
  }
  root.StudioChatMentions = {create};
})(window);
