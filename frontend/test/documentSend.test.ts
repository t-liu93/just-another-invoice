import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildDocumentSendPayload,
  invoiceDocumentSendType,
  resolveDocumentSendInitialLocale,
} from '../src/utils/documentSend.ts'

test('formal invoice kinds select their matching typed template key', () => {
  assert.equal(invoiceDocumentSendType('STANDARD'), 'invoice')
  assert.equal(invoiceDocumentSendType('ADVANCE'), 'advance')
  assert.equal(invoiceDocumentSendType('FINAL'), 'final')
  assert.equal(invoiceDocumentSendType('CREDIT_NOTE'), 'credit_note')
})

test('untouched formal send payload preserves issue-snapshot locale and template', () => {
  const template = { subject: 'Credit {INVOICE_NUMBER}', body: 'Credit body' }
  // The UI may currently display a changed Customer's zh locale, but it must
  // not turn that into an override when the user has not selected a locale.
  const payload = buildDocumentSendPayload({
    type: 'credit_note',
    to: 'customer@example.test',
    cc: '',
    locale: 'zh',
    localeWasChosen: false,
    subject: template.subject,
    body: template.body,
    template,
  })
  assert.deepEqual(payload, { to: 'customer@example.test', cc: null })
})

test('formal dialog initial baseline is the issue snapshot for every kind', () => {
  for (const type of ['invoice', 'advance', 'final', 'credit_note'] as const) {
    assert.equal(resolveDocumentSendInitialLocale({
      type,
      snapshotLocale: 'en',
      fallbackLocale: 'zh',
    }), 'en')
  }
  assert.equal(resolveDocumentSendInitialLocale({
    type: 'quote', snapshotLocale: null, fallbackLocale: 'zh',
  }), 'zh')
})

test('a body-only formal edit keeps the snapshot-language subject/PDF baseline', () => {
  for (const [type, subject, body] of [
    ['invoice', 'Invoice EN', 'Invoice body EN'],
    ['advance', 'Advance EN', 'Advance body EN'],
    ['final', 'Final EN', 'Final body EN'],
    ['credit_note', 'Credit EN', 'Credit body EN'],
  ] as const) {
    const payload = buildDocumentSendPayload({
      type,
      to: 'customer@example.test',
      cc: '',
      locale: resolveDocumentSendInitialLocale({
        type, snapshotLocale: 'en', fallbackLocale: 'zh',
      }),
      localeWasChosen: false,
      subject,
      body: `${body}\nPersonal note`,
      template: { subject, body },
    })
    assert.deepEqual(payload, {
      to: 'customer@example.test', cc: null, body: `${body}\nPersonal note`,
    })
  }
})

test('formal send payload includes only intentional locale and body overrides', () => {
  const template = { subject: 'Advance {INVOICE_NUMBER}', body: 'Advance body' }
  const payload = buildDocumentSendPayload({
    type: 'advance',
    to: 'customer@example.test',
    cc: 'copy@example.test',
    locale: 'zh',
    localeWasChosen: true,
    subject: template.subject,
    body: 'Personal note',
    template,
  })
  assert.deepEqual(payload, {
    to: 'customer@example.test',
    cc: 'copy@example.test',
    locale: 'zh',
    body: 'Personal note',
  })
})

test('quote and receipt keep their established explicit payloads', () => {
  const template = { subject: 'Receipt', body: 'Body' }
  const payload = buildDocumentSendPayload({
    type: 'receipt',
    to: 'customer@example.test',
    cc: '',
    locale: 'zh',
    localeWasChosen: false,
    subject: template.subject,
    body: template.body,
    template,
  })
  assert.deepEqual(payload, {
    to: 'customer@example.test', cc: null, locale: 'zh',
    subject: 'Receipt', body: 'Body',
  })
})
