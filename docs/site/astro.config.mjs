import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://pete-builds.github.io',
  base: '/open-model-arena',
  integrations: [
    starlight({
      title: 'Open Model Arena',
      description:
        'Blind, cost-aware model comparison for any OpenAI-compatible endpoint. Self-hosted. ELO leaderboard. Eval suites. LLM-as-judge.',
      social: [
        {
          icon: 'github',
          label: 'GitHub',
          href: 'https://github.com/pete-builds/open-model-arena',
        },
      ],
      sidebar: [
        { label: 'Getting Started', link: '/getting-started/' },
        {
          label: 'Guides',
          items: [
            { label: 'Eval Suites', link: '/guides/eval-suites/' },
            { label: 'LLM-as-Judge', link: '/guides/judge-mode/' },
            { label: 'Headless API', link: '/guides/headless-api/' },
            { label: 'Deployment', link: '/guides/deployment/' },
            { label: 'Auto-Deploy', link: '/guides/autodeploy/' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'API', link: '/reference/api/' },
            { label: 'Architecture', link: '/reference/architecture/' },
            { label: 'ELO Math', link: '/reference/elo-math/' },
            { label: 'Threat Model', link: '/reference/threat-model/' },
            { label: 'Metrics', link: '/reference/metrics/' },
          ],
        },
        { label: 'Changelog', link: '/changelog/' },
      ],
      editLink: {
        baseUrl:
          'https://github.com/pete-builds/open-model-arena/edit/main/docs/site/',
      },
    }),
  ],
});
