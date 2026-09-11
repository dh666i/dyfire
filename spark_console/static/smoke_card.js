/** Reusable material component. Enhances existing semantic markup without moving content. */
export class Card {
  static instances = new WeakMap();

  static create({ tag = 'section', className = '', content } = {}) {
    if (!['section', 'article', 'div', 'aside', 'button'].includes(tag)) throw new Error('Unsupported Card element');
    const element = document.createElement(tag);
    element.className = className;
    if (tag === 'button') element.type = 'button';
    if (content) element.append(content);
    new Card(element);
    return element;
  }

  constructor(element) {
    const existing = Card.instances.get(element);
    if (existing) return existing;
    this.element = element;
    this.frame = 0;
    this.point = null;
    this.motion = matchMedia('(prefers-reduced-motion: reduce)');
    this.pointer = matchMedia('(hover: hover) and (pointer: fine)');
    element.classList.add('card');
    element.dataset.card = '';
    // Nested panels share material, but never stack expensive background blurs.
    element.classList.toggle('card-nested', Boolean(element.parentElement?.closest('.card, dialog')));
    this.move = event => {
      if (event.pointerType === 'touch' || !this.pointer.matches || this.motion.matches) return;
      this.point = { x: event.clientX, y: event.clientY };
      if (this.frame) return;
      this.frame = requestAnimationFrame(() => {
        this.frame = 0;
        if (!this.point || !element.isConnected) return;
        const rect = element.getBoundingClientRect();
        const x = Math.max(0, Math.min(rect.width, this.point.x - rect.left));
        const y = Math.max(0, Math.min(rect.height, this.point.y - rect.top));
        element.style.setProperty('--spot-x', `${x}px`);
        element.style.setProperty('--spot-y', `${y}px`);
        element.dataset.spot = 'on';
      });
    };
    this.leave = () => {
      cancelAnimationFrame(this.frame);
      this.frame = 0;
      this.point = null;
      delete element.dataset.spot;
    };
    element.addEventListener('pointermove', this.move, { passive: true });
    element.addEventListener('pointerleave', this.leave);
    element.addEventListener('pointercancel', this.leave);
    this.motion.addEventListener('change', this.leave);
    this.pointer.addEventListener('change', this.leave);
    Card.instances.set(element, this);
  }

  destroy() {
    this.leave();
    this.element.removeEventListener('pointermove', this.move);
    this.element.removeEventListener('pointerleave', this.leave);
    this.element.removeEventListener('pointercancel', this.leave);
    this.motion.removeEventListener('change', this.leave);
    this.pointer.removeEventListener('change', this.leave);
    Card.instances.delete(this.element);
  }
}

export function mountCards(root) {
  return [...root.querySelectorAll('.hero, .today-card, .panel, .metrics > button, .member-note, .workspace, .account-choice')]
    .map(element => new Card(element));
}
