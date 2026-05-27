/* ─────────────────────────────────────────────────────────────────────────────
   RADIOMAN — Public Website Script
   ───────────────────────────────────────────────────────────────────────────── */

(function () {
    'use strict';

    /* ── Theme ──────────────────────────────────────────────────────────────── */
    const html = document.documentElement;
    const themeToggle = document.getElementById('themeToggle');

    function getStoredTheme() {
        return localStorage.getItem('rm-theme');
    }

    function applyTheme(theme) {
        html.setAttribute('data-theme', theme);
        localStorage.setItem('rm-theme', theme);
    }

    function toggleTheme() {
        const current = html.getAttribute('data-theme');
        applyTheme(current === 'dark' ? 'light' : 'dark');
    }

    // Apply stored theme or system preference on load
    (function initTheme() {
        const stored = getStoredTheme();
        if (stored) {
            applyTheme(stored);
        } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
            applyTheme('light');
        } else {
            applyTheme('dark');
        }
    })();

    if (themeToggle) {
        themeToggle.addEventListener('click', toggleTheme);
    }

    // Watch for OS theme change (only if no stored preference)
    if (window.matchMedia) {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
            if (!getStoredTheme()) {
                applyTheme(e.matches ? 'dark' : 'light');
            }
        });
    }

    /* ── Nav scroll behavior ────────────────────────────────────────────────── */
    const nav = document.getElementById('nav');

    function updateNavScroll() {
        if (window.scrollY > 10) {
            nav.classList.add('nav--scrolled');
        } else {
            nav.classList.remove('nav--scrolled');
        }
    }

    window.addEventListener('scroll', updateNavScroll, { passive: true });
    updateNavScroll();

    /* ── Mobile nav ──────────────────────────────────────────────────────────── */
    const hamburger = document.getElementById('navHamburger');
    const mobileNav = document.getElementById('navMobile');

    function openMobileNav() {
        hamburger.classList.add('open');
        hamburger.setAttribute('aria-expanded', 'true');
        mobileNav.classList.add('open');
        mobileNav.removeAttribute('aria-hidden');
        document.body.style.overflow = 'hidden';
    }

    function closeMobileNav() {
        hamburger.classList.remove('open');
        hamburger.setAttribute('aria-expanded', 'false');
        mobileNav.classList.remove('open');
        mobileNav.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
    }

    if (hamburger && mobileNav) {
        hamburger.addEventListener('click', () => {
            if (mobileNav.classList.contains('open')) {
                closeMobileNav();
            } else {
                openMobileNav();
            }
        });

        // Close on link click
        mobileNav.querySelectorAll('.nav__link, .btn').forEach((link) => {
            link.addEventListener('click', closeMobileNav);
        });
    }

    /* ── Smooth scroll for anchor links ────────────────────────────────────── */
    document.querySelectorAll('a[href^="#"]').forEach((link) => {
        link.addEventListener('click', function (e) {
            const target = document.querySelector(this.getAttribute('href'));
            if (!target) return;
            e.preventDefault();
            const navHeight = nav ? nav.offsetHeight : 0;
            const targetTop = target.getBoundingClientRect().top + window.scrollY - navHeight - 16;
            window.scrollTo({ top: targetTop, behavior: 'smooth' });
        });
    });

    /* ── Reveal on scroll ───────────────────────────────────────────────────── */
    function setupRevealObserver() {
        if (!('IntersectionObserver' in window)) {
            // Fallback: show everything immediately
            document.querySelectorAll('.reveal').forEach((el) => el.classList.add('visible'));
            return;
        }

        const observer = new IntersectionObserver(
            (entries) => {
                entries.forEach((entry) => {
                    if (entry.isIntersecting) {
                        entry.target.classList.add('visible');
                        observer.unobserve(entry.target);
                    }
                });
            },
            { threshold: 0.08, rootMargin: '0px 0px -40px 0px' }
        );

        document.querySelectorAll('.reveal').forEach((el) => observer.observe(el));
    }

    setupRevealObserver();

    /* ── Capabilities tabs ──────────────────────────────────────────────────── */
    function setupTabs() {
        const tabNav = document.querySelector('.tab-nav');
        if (!tabNav) return;

        const buttons = tabNav.querySelectorAll('.tab-nav__btn');
        const panels = document.querySelectorAll('.tab-panel');

        buttons.forEach((btn) => {
            btn.addEventListener('click', () => {
                const targetTab = btn.dataset.tab;

                buttons.forEach((b) => {
                    b.classList.remove('active');
                    b.setAttribute('aria-selected', 'false');
                });
                panels.forEach((p) => p.classList.remove('active'));

                btn.classList.add('active');
                btn.setAttribute('aria-selected', 'true');

                const targetPanel = document.getElementById('tab-' + targetTab);
                if (targetPanel) {
                    targetPanel.classList.add('active');
                }
            });

            // Keyboard navigation
            btn.addEventListener('keydown', (e) => {
                const btns = Array.from(buttons);
                const idx = btns.indexOf(btn);
                if (e.key === 'ArrowRight') {
                    e.preventDefault();
                    btns[(idx + 1) % btns.length].focus();
                } else if (e.key === 'ArrowLeft') {
                    e.preventDefault();
                    btns[(idx - 1 + btns.length) % btns.length].focus();
                }
            });
        });
    }

    setupTabs();

    /* ── Hero device — animate in on page load ──────────────────────────────── */
    function animateDevicePanel() {
        const devicePanel = document.querySelector('.hero__device-panel');
        if (!devicePanel) return;

        // After a short delay, "boot" the device panel with a subtle status change
        setTimeout(() => {
            const sessionVal = devicePanel.querySelector('.device-value--standby');
            if (sessionVal) {
                sessionVal.style.transition = 'color 0.5s ease';
            }
        }, 1200);
    }

    animateDevicePanel();

    /* ── Staggered grid card reveals ────────────────────────────────────────── */
    function setupStaggeredReveal() {
        const grids = [
            '.problem__grid',
            '.use-cases__grid',
            '.docs__grid',
        ];

        grids.forEach((selector) => {
            const grid = document.querySelector(selector);
            if (!grid) return;

            const cards = grid.querySelectorAll('.reveal');
            cards.forEach((card, idx) => {
                card.style.transitionDelay = (idx * 0.06) + 's';
            });
        });

        const workflowSteps = document.querySelectorAll('.workflow__step.reveal');
        workflowSteps.forEach((step, idx) => {
            step.style.transitionDelay = (idx * 0.1) + 's';
        });
    }

    setupStaggeredReveal();

    /* ── Close mobile nav on resize ──────────────────────────────────────────── */
    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            if (window.innerWidth > 640 && mobileNav && mobileNav.classList.contains('open')) {
                closeMobileNav();
            }
        }, 100);
    });

})();
