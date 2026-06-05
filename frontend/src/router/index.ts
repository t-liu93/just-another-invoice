import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: '/dashboard',
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('../views/Register.vue'),
    meta: { public: true },
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/Login.vue'),
    meta: { public: true },
  },
  {
    path: '/forgot-password',
    name: 'forgot-password',
    component: () => import('../views/ForgotPassword.vue'),
    meta: { public: true },
  },
  {
    path: '/reset-password',
    name: 'reset-password',
    component: () => import('../views/ResetPassword.vue'),
    meta: { public: true },
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('../views/Dashboard.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/settings/smtp',
    name: 'smtp-settings',
    component: () => import('../views/settings/SmtpSettings.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/dashboard',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Navigation guard.
router.beforeEach(async (to) => {
  const auth = useAuthStore()

  // Initialise the store once (fetch bootstrap + try to fetch user).
  await auth.initialise()

  const isPublic = to.meta.public === true

  // If registration is open and user is not logged in, redirect to register.
  if (auth.bootstrap?.registration_open && !auth.user && to.name !== 'register') {
    return { name: 'register' }
  }

  // If registration is closed, don't show the register page.
  if (!auth.bootstrap?.registration_open && to.name === 'register') {
    return { name: 'login' }
  }

  // If not authenticated and page requires auth, redirect to login.
  if (!auth.user && !isPublic) {
    return { name: 'login' }
  }

  // If authenticated, don't show login/register pages.
  if (auth.user && isPublic && to.name !== 'dashboard') {
    return { name: 'dashboard' }
  }
})

export default router
