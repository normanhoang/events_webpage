// Keep local artwork visible underneath slow, blocked, or broken remote images.
// Never replace src: a missing fallback cannot create an error/retry loop.
document.querySelectorAll('[data-remote-image]').forEach((image) => {
  const showFallback = () => { image.hidden = true; };
  image.addEventListener('error', showFallback, { once: true });
  if (image.complete && image.naturalWidth === 0) showFallback();
});
