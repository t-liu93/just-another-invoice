<script setup lang="ts">
/**
 * Shared application top bar.
 *
 * Left: brand + a horizontal navigation menu (Dashboard / Customers …).
 * Right: a single gear opens the unified settings panel; a separate Company
 * icon goes to the business-identity page; plus a quick language toggle, the
 * current user, and logout.
 */
import { computed, h, type Component } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useI18n } from 'vue-i18n'
import { useSettingsPanel } from '../composables/useSettingsPanel'
import { useLocale } from '../composables/useLocale'
import {
  SettingsOutline,
  BusinessOutline,
  LibraryOutline,
  GlobeOutline,
  LogOutOutline,
  PeopleOutline,
  GridOutline,
  CubeOutline,
  DocumentTextOutline,
  ReceiptOutline,
  CalculatorOutline,
  WalletOutline,
  CardOutline,
  RepeatOutline,
  BarChartOutline,
} from '@vicons/ionicons5'
import { NIcon, type MenuOption } from 'naive-ui'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const { t } = useI18n()
const { open } = useSettingsPanel()
const { currentLocale, setLocale } = useLocale()

function renderIcon(icon: Component) {
  return () => h(NIcon, null, { default: () => h(icon) })
}

const navOptions = computed<MenuOption[]>(() => [
  { label: t('nav.dashboard'), key: 'dashboard', icon: renderIcon(GridOutline) },
  { label: t('nav.customers'), key: 'customer-list', icon: renderIcon(PeopleOutline) },
  { label: t('nav.products'), key: 'product-list', icon: renderIcon(CubeOutline) },
  { label: t('nav.invoices'), key: 'invoice-list', icon: renderIcon(DocumentTextOutline) },
  { label: t('nav.quotes'), key: 'quote-list', icon: renderIcon(ReceiptOutline) },
  { label: t('nav.estimates'), key: 'estimate-list', icon: renderIcon(CalculatorOutline) },
  { label: t('nav.payments'), key: 'payment-list', icon: renderIcon(WalletOutline) },
  { label: t('nav.expenses'), key: 'expense-list', icon: renderIcon(CardOutline) },
  { label: t('nav.recurring'), key: 'recurring-expense-list', icon: renderIcon(RepeatOutline) },
  {
    label: t('nav.reports'),
    key: 'reports-group',
    icon: renderIcon(BarChartOutline),
    children: [
      { label: t('reports.pl.title'), key: 'report-profit-loss' },
      { label: t('reports.vat.navLink'), key: 'report-vat-return' },
    ],
  },
])

/** Highlight the active top-level section based on the current route name. */
const activeKey = computed<string | null>(() => {
  const name = route.name
  if (typeof name === 'string' && name.startsWith('customer')) return 'customer-list'
  if (typeof name === 'string' && name.startsWith('product')) return 'product-list'
  if (typeof name === 'string' && name.startsWith('invoice')) return 'invoice-list'
  if (typeof name === 'string' && name.startsWith('quote')) return 'quote-list'
  if (typeof name === 'string' && name.startsWith('estimate')) return 'estimate-list'
  if (typeof name === 'string' && name.startsWith('payment')) return 'payment-list'
  if (typeof name === 'string' && name.startsWith('recurring-expense')) return 'recurring-expense-list'
  if (typeof name === 'string' && name.startsWith('expense')) return 'expense-list'
  if (name === 'report-profit-loss') return 'report-profit-loss'
  if (name === 'report-vat-return') return 'report-vat-return'
  if (name === 'dashboard') return 'dashboard'
  return null
})

function handleNav(key: string) {
  if (key === 'dashboard') router.push('/dashboard')
  else if (key === 'customer-list') router.push('/customers')
  else if (key === 'product-list') router.push('/products')
  else if (key === 'invoice-list') router.push('/invoices')
  else if (key === 'quote-list') router.push('/quotes')
  else if (key === 'estimate-list') router.push('/estimates')
  else if (key === 'payment-list') router.push('/payments')
  else if (key === 'expense-list') router.push('/expenses')
  else if (key === 'recurring-expense-list') router.push('/recurring-expenses')
  else if (key === 'report-profit-loss') router.push('/reports/profit-loss')
  else if (key === 'report-vat-return') router.push('/reports/vat-return')
}

function toggleLocale() {
  void setLocale(currentLocale.value === 'en' ? 'zh' : 'en')
}

async function handleLogout() {
  await auth.logout()
  router.push('/login')
}
</script>

<template>
  <n-layout-header bordered class="app-header">
    <div class="header-left">
      <h2 class="app-title" @click="router.push('/dashboard')">{{ t('app.title') }}</h2>
      <n-menu
        mode="horizontal"
        :options="navOptions"
        :value="activeKey"
        class="header-nav"
        @update:value="handleNav"
      />
    </div>
    <div class="header-right">
      <n-button quaternary size="small" :title="t('settings.panel.open')" @click="open()">
        <template #icon>
          <n-icon><SettingsOutline /></n-icon>
        </template>
      </n-button>
      <n-button quaternary size="small" :title="t('settings.panel.company')" @click="router.push('/settings/company')">
        <template #icon>
          <n-icon><BusinessOutline /></n-icon>
        </template>
      </n-button>
      <n-button quaternary size="small" :title="t('nav.contentLibrary')" @click="router.push('/content-library')">
        <template #icon>
          <n-icon><LibraryOutline /></n-icon>
        </template>
      </n-button>
      <n-button quaternary size="small" @click="toggleLocale">
        <template #icon>
          <n-icon><GlobeOutline /></n-icon>
        </template>
        {{ currentLocale === 'en' ? '中文' : 'EN' }}
      </n-button>
      <n-tag v-if="auth.user" size="small" type="info">
        {{ auth.user.email }}
      </n-tag>
      <n-button quaternary size="small" type="error" @click="handleLogout">
        <template #icon>
          <n-icon><LogOutOutline /></n-icon>
        </template>
      </n-button>
    </div>
  </n-layout-header>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 24px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 24px;
}

.header-left .app-title {
  margin: 0;
  cursor: pointer;
  font-size: 18px;
  white-space: nowrap;
}

.header-nav {
  background: transparent;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
