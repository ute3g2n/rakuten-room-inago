(() => {
  'use strict';
  document.documentElement.classList.add('enhanced');
  const chapters = [...document.querySelectorAll('.chapter')];
  const parts = [...document.querySelectorAll('.part')];
  const details = [...document.querySelectorAll('.normative, .reference-document, .glossary-group, .flow-transcript, .implementation-reference')];
  if (window.matchMedia('(max-width: 800px)').matches) {
    details.filter(item => item.classList.contains('flow-transcript')).forEach(item => { item.open = true; });
  }
  const search = document.querySelector('#document-search');
  const counter = document.querySelector('#result-count');
  const empty = document.querySelector('#no-results');
  const navLinks = [...document.querySelectorAll('.rail a[data-chapter]')];
  const normalize = text => text.normalize('NFKC').toLocaleLowerCase('ja').replace(/\s+/g, ' ').trim();
  const texts = new Map(chapters.map(chapter => [chapter, normalize(chapter.textContent)]));
  let searchState = null;
  const updateSearch = () => {
    const words = normalize(search.value).split(' ').filter(Boolean);
    if (words.length && searchState === null) searchState = new Map(details.map(item => [item, item.open]));
    let matches = 0;
    for (const chapter of chapters) {
      const matched = words.every(word => texts.get(chapter).includes(word));
      chapter.hidden = !matched;
      if (matched) matches += 1;
      if (words.length && matched) {
        chapter.querySelector('details').open = true;
        chapter.querySelectorAll('.glossary-group, .implementation-reference').forEach(item => { item.open = true; });
      }
    }
    parts.forEach(part => { part.hidden = !chapters.some(chapter => chapter.dataset.part === part.dataset.part && !chapter.hidden); });
    navLinks.forEach(link => { link.hidden = document.getElementById(link.dataset.chapter).hidden; });
    empty.hidden = matches > 0;
    counter.textContent = words.length ? `${matches} / ${chapters.length}章が一致（複数語はすべて含む章）` : `全${chapters.length}章を表示。IR / AT番号や「予約」「17:45」でも検索できます。`;
    if (!words.length && searchState !== null) {
      for (const [item, wasOpen] of searchState) item.open = wasOpen;
      searchState = null;
    }
  };
  search.addEventListener('input', updateSearch);
  const referenceSearch = document.querySelector('#reference-search');
  const references = [...document.querySelectorAll('.reference-document')];
  const referenceTexts = new Map(references.map(item => [item, normalize(item.textContent)]));
  const filterReferences = () => {
    const words = normalize(referenceSearch.value).split(' ').filter(Boolean);
    let count = 0;
    for (const item of references) {
      item.hidden = !words.every(word => referenceTexts.get(item).includes(word));
      if (!item.hidden) count += 1;
    }
    document.querySelector('#reference-count').textContent = words.length ? `${count} / ${references.length}資料が一致` : `全${references.length}資料`;
  };
  referenceSearch.addEventListener('input', filterReferences);
  document.querySelector('#clear-search').addEventListener('click', () => { search.value = ''; updateSearch(); search.focus(); });
  document.querySelector('#expand-all').addEventListener('click', () => { details.forEach(item => { item.open = true; }); });
  document.querySelector('#collapse-all').addEventListener('click', () => { details.forEach(item => { item.open = false; }); });
  document.querySelector('#print-document').addEventListener('click', () => window.print());
  let printState = null;
  window.addEventListener('beforeprint', () => {
    if (printState === null) printState = new Map(details.map(item => [item, item.open]));
    details.forEach(item => { item.open = true; });
  });
  window.addEventListener('afterprint', () => {
    if (printState) for (const [item, wasOpen] of printState) item.open = wasOpen;
    printState = null;
  });
  const revealHash = () => {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch { return; }
    const target = document.getElementById(id);
    if (!target) return;
    if (target.hidden && target.classList.contains('reference-document')) { referenceSearch.value = ''; filterReferences(); }
    else if (target.hidden) { search.value = ''; updateSearch(); }
    let parent = target;
    while (parent) { if (parent.tagName === 'DETAILS') parent.open = true; parent = parent.parentElement; }
    requestAnimationFrame(() => target.scrollIntoView({ block: 'start' }));
  };
  window.addEventListener('hashchange', revealHash);
  if (location.hash) revealHash();
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
      if (!visible) return;
      navLinks.forEach(link => {
        if (link.dataset.chapter === visible.target.id) link.setAttribute('aria-current', 'location');
        else link.removeAttribute('aria-current');
      });
    }, { rootMargin: '-5% 0px -65% 0px', threshold: 0 });
    chapters.forEach(chapter => observer.observe(chapter));
  }
})();
