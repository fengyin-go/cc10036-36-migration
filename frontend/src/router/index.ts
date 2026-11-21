import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import TimeMigrationView from '@/views/TimeMigrationView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/time-migration', name: 'time-migration', component: TimeMigrationView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
