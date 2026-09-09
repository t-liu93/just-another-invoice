<script setup lang="ts">
/** Displays backend-provided VAT snapshots without performing money math. */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { components } from '../api/schema'

type PaymentRead = components['schemas']['PaymentRead']

const props = defineProps<{ payment: PaymentRead }>()
const { t } = useI18n()

const hasDepositVatSplit = computed(() =>
  props.payment.deposit_taxable_amount != null
  && props.payment.deposit_vat_amount != null,
)
const taxBreakdown = computed(() => props.payment.tax_breakdown ?? [])

function fmtMoney(value: string | number): string {
  return Number(value).toFixed(2)
}
</script>

<template>
  <div v-if="hasDepositVatSplit" class="deposit-vat-breakdown" data-testid="deposit-vat-breakdown">
    <div class="deposit-vat-title">{{ t('payments.depositVatSplit') }}</div>
    <div class="deposit-vat-summary">
      <span>{{ t('payments.depositGrossReceived') }}: {{ fmtMoney(payment.amount) }}</span>
      <span>{{ t('payments.depositTaxableAmount') }}: {{ fmtMoney(payment.deposit_taxable_amount!) }}</span>
      <span>{{ t('payments.depositVatAmount') }}: {{ fmtMoney(payment.deposit_vat_amount!) }}</span>
    </div>
    <div v-if="taxBreakdown.length" class="deposit-vat-buckets">
      <div v-for="tax in taxBreakdown" :key="`${tax.vat_rate_label}-${tax.vat_rate_percent}`">
        {{ t('payments.depositVatBucket', {
          rate: tax.vat_rate_percent,
          taxable: fmtMoney(tax.taxable_amount),
          vat: fmtMoney(tax.vat_amount),
          gross: fmtMoney(tax.gross_amount),
        }) }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.deposit-vat-breakdown { display: grid; gap: 4px; margin-top: 6px; color: var(--n-text-color-3); font-size: 12px; }
.deposit-vat-title { color: var(--n-text-color-2); font-weight: 600; }
.deposit-vat-summary, .deposit-vat-buckets { display: flex; flex-wrap: wrap; gap: 4px 12px; }
.deposit-vat-buckets { display: grid; gap: 2px; }
</style>
