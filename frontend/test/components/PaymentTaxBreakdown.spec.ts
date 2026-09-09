import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import en from '../../src/locales/en.json'
import PaymentTaxBreakdown from '../../src/components/PaymentTaxBreakdown.vue'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

function payment(overrides: Record<string, unknown> = {}) {
  return {
    amount: '200.00',
    deposit_taxable_amount: '165.29',
    deposit_vat_amount: '34.71',
    tax_breakdown: [{
      vat_rate_label: 'VAT 21%', vat_rate_percent: '21', taxable_amount: '165.29',
      vat_amount: '34.71', gross_amount: '200.00',
    }],
    ...overrides,
  }
}

describe('PaymentTaxBreakdown', () => {
  it('renders server totals for a single-rate quote deposit without deriving VAT', () => {
    const wrapper = mount(PaymentTaxBreakdown, {
      props: { payment: payment() as never }, global: { plugins: [i18n] },
    })

    expect(wrapper.text()).toContain('Received (incl. VAT): 200.00')
    expect(wrapper.text()).toContain('Net taxable amount: 165.29')
    expect(wrapper.text()).toContain('VAT: 34.71')
    expect(wrapper.text()).toContain('VAT 21%: net 165.29 · VAT 34.71 · gross 200.00')
  })

  it('renders each persisted mixed-rate bucket and preserves a 0% VAT deposit', () => {
    const wrapper = mount(PaymentTaxBreakdown, {
      props: {
        payment: payment({
          amount: '150.00', deposit_taxable_amount: '140.00', deposit_vat_amount: '10.00',
          tax_breakdown: [
            { vat_rate_label: 'VAT 21%', vat_rate_percent: '21', taxable_amount: '100.00', vat_amount: '21.00', gross_amount: '121.00' },
            { vat_rate_label: 'VAT 0%', vat_rate_percent: '0', taxable_amount: '29.00', vat_amount: '0.00', gross_amount: '29.00' },
          ],
        }) as never,
      },
      global: { plugins: [i18n] },
    })

    expect(wrapper.text()).toContain('VAT 21%: net 100.00 · VAT 21.00 · gross 121.00')
    expect(wrapper.text()).toContain('VAT 0%: net 29.00 · VAT 0.00 · gross 29.00')
  })

  it('does not render a split for an ordinary payment or refund', () => {
    for (const value of [null, undefined]) {
      const wrapper = mount(PaymentTaxBreakdown, {
        props: { payment: payment({ deposit_taxable_amount: value, deposit_vat_amount: value }) as never },
        global: { plugins: [i18n] },
      })
      expect(wrapper.find('[data-testid="deposit-vat-breakdown"]').exists()).toBe(false)
    }
  })
})
