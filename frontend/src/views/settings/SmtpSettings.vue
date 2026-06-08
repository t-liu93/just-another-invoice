<script setup lang="ts">
/**
 * SMTP settings route — kept primarily for the onboarding flow, which reaches
 * it at /settings/smtp?from=onboarding (the router guard whitelists this route
 * before onboarding is complete).  Post-onboarding, SMTP is edited inside the
 * unified settings panel instead; this thin page just wraps the reusable
 * SmtpSettingsForm with minimal navigation chrome.
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import SmtpSettingsForm from '../../components/settings/SmtpSettingsForm.vue'

const router = useRouter()
const route = useRoute()
const { t } = useI18n()

const isFromOnboarding = computed(() => route.query.from === 'onboarding')

function handleBack() {
  if (isFromOnboarding.value) {
    router.push({ name: 'onboarding', query: { resume: 'done' } })
    return
  }
  router.push('/dashboard')
}
</script>

<template>
  <div class="settings-page">
    <n-layout>
      <n-layout-header bordered class="app-header">
        <n-button quaternary size="small" @click="handleBack">
          ← {{ isFromOnboarding ? t('onboarding.returnToOnboarding') : t('app.title') }}
        </n-button>
      </n-layout-header>

      <n-layout-content class="app-content">
        <div class="smtp-settings">
          <n-card :title="t('settings.smtp.title')">
            <SmtpSettingsForm />
            <div v-if="isFromOnboarding" class="onboarding-return">
              <n-button @click="handleBack">
                {{ t('onboarding.returnToOnboarding') }}
              </n-button>
            </div>
          </n-card>
        </div>
      </n-layout-content>
    </n-layout>
  </div>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  padding: 8px 24px;
}

.app-content {
  min-height: calc(100vh - 57px);
}

.smtp-settings {
  max-width: 600px;
  margin: 24px auto;
  padding: 0 24px;
}

.onboarding-return {
  margin-top: 16px;
}
</style>
