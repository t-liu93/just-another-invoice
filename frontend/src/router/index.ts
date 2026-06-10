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
    path: '/onboarding',
    name: 'onboarding',
    component: () => import('../views/Onboarding.vue'),
    meta: { requiresAuth: true, isOnboarding: true },
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
    path: '/settings/company',
    name: 'company-profile',
    component: () => import('../views/settings/CompanyProfile.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/customers',
    name: 'customer-list',
    component: () => import('../views/customers/CustomerList.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/customers/new',
    name: 'customer-new',
    component: () => import('../views/customers/CustomerEdit.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/customers/:id/edit',
    name: 'customer-edit',
    component: () => import('../views/customers/CustomerEdit.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/products',
    name: 'product-list',
    component: () => import('../views/products/ProductList.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/products/new',
    name: 'product-new',
    component: () => import('../views/products/ProductEdit.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/products/:id/edit',
    name: 'product-edit',
    component: () => import('../views/products/ProductEdit.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/invoices',
    name: 'invoice-list',
    component: () => import('../views/invoices/InvoiceList.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/invoices/new',
    name: 'invoice-new',
    component: () => import('../views/invoices/InvoiceEdit.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/invoices/:id/edit',
    name: 'invoice-edit',
    component: () => import('../views/invoices/InvoiceEdit.vue'),
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
  const isOnboarding = to.meta.isOnboarding === true
  const isOnboardingResume = isOnboarding && to.query.resume === 'done'

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

  // If authenticated but onboarding is not completed, force the onboarding flow.
  // Allow the onboarding page itself and SMTP settings (reachable from onboarding)
  // but block all other authenticated pages until onboarding is done.
  if (
    auth.user &&
    auth.bootstrap &&
    !auth.bootstrap.onboarding_completed &&
    !isOnboarding &&
    to.name !== 'smtp-settings'
  ) {
    return { name: 'onboarding' }
  }

  // If authenticated and onboarding is completed, don't show the onboarding page.
  if (auth.user && auth.bootstrap?.onboarding_completed && isOnboarding && !isOnboardingResume) {
    return { name: 'dashboard' }
  }

  // If authenticated, don't show login/register pages.
  if (auth.user && isPublic && to.name !== 'dashboard') {
    return { name: 'dashboard' }
  }
})

export default router
