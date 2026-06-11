<script setup lang="ts">
import { onMounted, ref, h, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  useMessage, useDialog,
  NButton, NSpace, NInput, NSelect, NSwitch, NTabs, NTabPane,
  NDataTable, NSpin, NModal, NForm, NFormItem, NTag,
  NText, NInputNumber,
} from 'naive-ui'
import { AddOutline, CreateOutline, TrashOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import AppHeader from '../../components/AppHeader.vue'
import { get, post, put, del } from '../../api/http'
import type { components } from '../../api/schema'

type DocumentTemplateRead = components['schemas']['DocumentTemplateRead']
type DocumentTemplateWrite = components['schemas']['DocumentTemplateWrite']
type DocumentTemplateLineWrite = components['schemas']['DocumentTemplateLineWrite']
type ContentBlockRead = components['schemas']['ContentBlockRead']
type ContentBlockWrite = components['schemas']['ContentBlockWrite']
type NoteTemplateRead = components['schemas']['NoteTemplateRead']
type NoteTemplateWrite = components['schemas']['NoteTemplateWrite']
type VatRateRead = components['schemas']['VatRateRead']

const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()

// ------------------------------------------------------------------ Document Templates

const templates = ref<DocumentTemplateRead[]>([])
const templatesLoading = ref(false)
const showTemplateModal = ref(false)
const editingTemplate = ref<DocumentTemplateRead | null>(null)
const templateSaving = ref(false)
const templateName = ref('')
const templateAppliesTo = ref<'QUOTE' | 'INVOICE' | 'BOTH'>('BOTH')

interface TemplateLineForm {
  name: string
  description: string | null
  quantity: number
  unit_id: string | null
  unit_name: string | null
  unit_price: number | null
  discount_type: 'NONE' | 'PERCENTAGE' | 'FIXED'
  discount_value: number
  vat_rate_id: string | null
}

const templateLines = ref<TemplateLineForm[]>([])

function emptyTemplateLine(): TemplateLineForm {
  return { name: '', description: null, quantity: 1, unit_id: null, unit_name: null, unit_price: null, discount_type: 'NONE', discount_value: 0, vat_rate_id: null }
}

async function loadTemplates() {
  templatesLoading.value = true
  try {
    templates.value = await get<DocumentTemplateRead[]>('/api/v1/document-templates')
  } catch {
    message.error(t('contentLibrary.loadFailed'))
  } finally {
    templatesLoading.value = false
  }
}

function openCreateTemplate() {
  editingTemplate.value = null
  templateName.value = ''
  templateAppliesTo.value = 'BOTH'
  templateLines.value = [emptyTemplateLine()]
  showTemplateModal.value = true
}

function openEditTemplate(tmpl: DocumentTemplateRead) {
  editingTemplate.value = tmpl
  templateName.value = tmpl.name
  templateAppliesTo.value = tmpl.applies_to
  templateLines.value = (tmpl.lines ?? []).map(l => ({
    name: l.name,
    description: l.description ?? null,
    quantity: Number(l.quantity),
    unit_id: l.unit_id ?? null,
    unit_name: l.unit_name ?? null,
    unit_price: l.unit_price != null ? Number(l.unit_price) : null,
    discount_type: (l.discount_type as 'NONE' | 'PERCENTAGE' | 'FIXED') ?? 'NONE',
    discount_value: Number(l.discount_value),
    vat_rate_id: l.vat_rate_id ?? null,
  }))
  showTemplateModal.value = true
}

async function saveTemplate() {
  if (!templateName.value.trim() || templateLines.value.length === 0) return
  templateSaving.value = true
  try {
    const body: DocumentTemplateWrite = {
      name: templateName.value.trim(),
      applies_to: templateAppliesTo.value,
      lines: templateLines.value.map((l) => {
        const lineWrite: DocumentTemplateLineWrite = {
          name: l.name.trim(),
          description: l.description ?? undefined,
          quantity: String(l.quantity),
          unit_id: l.unit_id ?? undefined,
          unit_name: l.unit_name ?? undefined,
          unit_price: l.unit_price != null ? String(l.unit_price) : undefined,
          discount_type: l.discount_type,
          discount_value: String(l.discount_value),
          vat_rate_id: l.vat_rate_id ?? undefined,
        }
        return lineWrite
      }),
    }
    if (editingTemplate.value) {
      await put(`/api/v1/document-templates/${editingTemplate.value.id}`, body)
      message.success(t('contentLibrary.template.saveSuccess'))
    } else {
      await post('/api/v1/document-templates', body)
      message.success(t('contentLibrary.template.createSuccess'))
    }
    showTemplateModal.value = false
    await loadTemplates()
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : t('contentLibrary.template.saveFailed'))
  } finally {
    templateSaving.value = false
  }
}

function deleteTemplate(tmpl: DocumentTemplateRead) {
  dialog.warning({
    title: t('contentLibrary.template.delete'),
    content: t('contentLibrary.template.deleteConfirm', { name: tmpl.name }),
    positiveText: t('contentLibrary.template.delete'),
    negativeText: t('contentLibrary.cancel'),
    onPositiveClick: async () => {
      try {
        await del(`/api/v1/document-templates/${tmpl.id}`)
        message.success(t('contentLibrary.template.deleteSuccess'))
        await loadTemplates()
      } catch {
        message.error(t('contentLibrary.template.deleteFailed'))
      }
    },
  })
}

const templateColumns = computed(() => [
  { title: t('contentLibrary.template.name'), key: 'name', ellipsis: { tooltip: true } },
  {
    title: t('contentLibrary.template.appliesTo'),
    key: 'applies_to',
    width: 120,
    align: 'center' as const,
    render(row: DocumentTemplateRead) {
      return h(NTag, { size: 'small', type: 'info' }, () => row.applies_to)
    },
  },
  {
    title: t('contentLibrary.template.lineCount'),
    key: 'lines',
    width: 80,
    align: 'center' as const,
    render(row: DocumentTemplateRead) {
      return String((row.lines ?? []).length)
    },
  },
  {
    title: t('contentLibrary.actions'),
    key: 'actions',
    width: 96,
    align: 'center' as const,
    render(row: DocumentTemplateRead) {
      return h(NSpace, { size: 4, justify: 'center', wrapItem: false }, () => [
        h(NButton, { size: 'small', quaternary: true, circle: true, onClick: () => openEditTemplate(row) },
          () => h(NIcon, null, { default: () => h(CreateOutline) })),
        h(NButton, { size: 'small', quaternary: true, circle: true, type: 'error', onClick: () => deleteTemplate(row) },
          () => h(NIcon, null, { default: () => h(TrashOutline) })),
      ])
    },
  },
])

// ------------------------------------------------------------------ Content Blocks

const blocks = ref<ContentBlockRead[]>([])
const blocksLoading = ref(false)
const showBlockModal = ref(false)
const editingBlock = ref<ContentBlockRead | null>(null)
const blockSaving = ref(false)
const blockKind = ref<'WARRANTY' | 'TERMS' | 'BANK' | 'PAYMENT_TERMS'>('WARRANTY')
const blockName = ref('')
const blockBody = ref('')
const blockIsDefault = ref(false)

const kindOptions = computed(() => [
  { label: t('contentLibrary.block.kindWARRANTY'), value: 'WARRANTY' },
  { label: t('contentLibrary.block.kindTERMS'), value: 'TERMS' },
  { label: t('contentLibrary.block.kindBANK'), value: 'BANK' },
  { label: t('contentLibrary.block.kindPAYMENT_TERMS'), value: 'PAYMENT_TERMS' },
])

async function loadBlocks() {
  blocksLoading.value = true
  try {
    blocks.value = await get<ContentBlockRead[]>('/api/v1/content-blocks')
  } catch {
    message.error(t('contentLibrary.loadFailed'))
  } finally {
    blocksLoading.value = false
  }
}

function openCreateBlock() {
  editingBlock.value = null
  blockKind.value = 'WARRANTY'
  blockName.value = ''
  blockBody.value = ''
  blockIsDefault.value = false
  showBlockModal.value = true
}

function openEditBlock(b: ContentBlockRead) {
  editingBlock.value = b
  blockKind.value = b.kind
  blockName.value = b.name
  blockBody.value = b.body
  blockIsDefault.value = b.is_default
  showBlockModal.value = true
}

async function saveBlock() {
  if (!blockName.value.trim() || !blockBody.value.trim()) return
  blockSaving.value = true
  try {
    const body: ContentBlockWrite = {
      kind: blockKind.value,
      name: blockName.value.trim(),
      body: blockBody.value.trim(),
      is_default: blockIsDefault.value,
    }
    if (editingBlock.value) {
      await put(`/api/v1/content-blocks/${editingBlock.value.id}`, body)
      message.success(t('contentLibrary.block.saveSuccess'))
    } else {
      await post('/api/v1/content-blocks', body)
      message.success(t('contentLibrary.block.createSuccess'))
    }
    showBlockModal.value = false
    await loadBlocks()
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : t('contentLibrary.block.saveFailed'))
  } finally {
    blockSaving.value = false
  }
}

function deleteBlock(b: ContentBlockRead) {
  dialog.warning({
    title: t('contentLibrary.block.delete'),
    content: t('contentLibrary.block.deleteConfirm', { name: b.name }),
    positiveText: t('contentLibrary.block.delete'),
    negativeText: t('contentLibrary.cancel'),
    onPositiveClick: async () => {
      try {
        await del(`/api/v1/content-blocks/${b.id}`)
        message.success(t('contentLibrary.block.deleteSuccess'))
        await loadBlocks()
      } catch {
        message.error(t('contentLibrary.block.deleteFailed'))
      }
    },
  })
}

const blockColumns = computed(() => [
  {
    title: t('contentLibrary.block.kind'),
    key: 'kind',
    width: 140,
    render(row: ContentBlockRead) {
      return h(NTag, { size: 'small' }, () => t(`contentLibrary.block.kind${row.kind}`))
    },
  },
  { title: t('contentLibrary.block.name'), key: 'name' },
  {
    title: t('contentLibrary.block.isDefault'),
    key: 'is_default',
    width: 80,
    render(row: ContentBlockRead) {
      return row.is_default ? h(NTag, { size: 'small', type: 'success' }, () => t('contentLibrary.yes')) : '—'
    },
  },
  {
    title: t('contentLibrary.block.body'),
    key: 'body',
    ellipsis: { tooltip: true },
  },
  {
    title: t('contentLibrary.actions'),
    key: 'actions',
    width: 80,
    render(row: ContentBlockRead) {
      return h(NSpace, { size: 4, justify: 'center', wrapItem: false }, () => [
        h(NButton, { size: 'small', quaternary: true, circle: true, onClick: () => openEditBlock(row) },
          () => h(NIcon, null, { default: () => h(CreateOutline) })),
        h(NButton, { size: 'small', quaternary: true, circle: true, type: 'error', onClick: () => deleteBlock(row) },
          () => h(NIcon, null, { default: () => h(TrashOutline) })),
      ])
    },
  },
])

// ------------------------------------------------------------------ Note Templates

const noteTemplates = ref<NoteTemplateRead[]>([])
const noteTemplatesLoading = ref(false)
const showNoteTemplateModal = ref(false)
const editingNoteTemplate = ref<NoteTemplateRead | null>(null)
const noteSaving = ref(false)
const noteName = ref('')
const noteBody = ref('')

async function loadNoteTemplates() {
  noteTemplatesLoading.value = true
  try {
    noteTemplates.value = await get<NoteTemplateRead[]>('/api/v1/note-templates')
  } catch {
    message.error(t('contentLibrary.loadFailed'))
  } finally {
    noteTemplatesLoading.value = false
  }
}

function openCreateNote() {
  editingNoteTemplate.value = null
  noteName.value = ''
  noteBody.value = ''
  showNoteTemplateModal.value = true
}

function openEditNote(nt: NoteTemplateRead) {
  editingNoteTemplate.value = nt
  noteName.value = nt.name
  noteBody.value = nt.body
  showNoteTemplateModal.value = true
}

async function saveNote() {
  if (!noteName.value.trim() || !noteBody.value.trim()) return
  noteSaving.value = true
  try {
    const body: NoteTemplateWrite = { name: noteName.value.trim(), body: noteBody.value.trim() }
    if (editingNoteTemplate.value) {
      await put(`/api/v1/note-templates/${editingNoteTemplate.value.id}`, body)
      message.success(t('contentLibrary.note.saveSuccess'))
    } else {
      await post('/api/v1/note-templates', body)
      message.success(t('contentLibrary.note.createSuccess'))
    }
    showNoteTemplateModal.value = false
    await loadNoteTemplates()
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : t('contentLibrary.note.saveFailed'))
  } finally {
    noteSaving.value = false
  }
}

function deleteNote(nt: NoteTemplateRead) {
  dialog.warning({
    title: t('contentLibrary.note.delete'),
    content: t('contentLibrary.note.deleteConfirm', { name: nt.name }),
    positiveText: t('contentLibrary.note.delete'),
    negativeText: t('contentLibrary.cancel'),
    onPositiveClick: async () => {
      try {
        await del(`/api/v1/note-templates/${nt.id}`)
        message.success(t('contentLibrary.note.deleteSuccess'))
        await loadNoteTemplates()
      } catch {
        message.error(t('contentLibrary.note.deleteFailed'))
      }
    },
  })
}

const noteColumns = computed(() => [
  { title: t('contentLibrary.note.name'), key: 'name', width: 200 },
  { title: t('contentLibrary.note.body'), key: 'body', ellipsis: { tooltip: true } },
  {
    title: t('contentLibrary.actions'),
    key: 'actions',
    width: 80,
    render(row: NoteTemplateRead) {
      return h(NSpace, { size: 4, justify: 'center', wrapItem: false }, () => [
        h(NButton, { size: 'small', quaternary: true, circle: true, onClick: () => openEditNote(row) },
          () => h(NIcon, null, { default: () => h(CreateOutline) })),
        h(NButton, { size: 'small', quaternary: true, circle: true, type: 'error', onClick: () => deleteNote(row) },
          () => h(NIcon, null, { default: () => h(TrashOutline) })),
      ])
    },
  },
])

// ------------------------------------------------------------------ VAT rates (for template line form)

const vatRates = ref<VatRateRead[]>([])

async function loadVatRates() {
  try {
    const res = await get<{ items: VatRateRead[] }>('/api/v1/vat-rates')
    vatRates.value = res.items
  } catch {
    // non-fatal
  }
}

const vatRateOptions = computed(() => vatRates.value.map(r => ({
  label: `${r.label} (${r.percent}%)`,
  value: r.id,
})))

const discountTypeOptions = [
  { label: 'None', value: 'NONE' },
  { label: 'Percentage (%)', value: 'PERCENTAGE' },
  { label: 'Fixed amount', value: 'FIXED' },
]

// ------------------------------------------------------------------ mount

onMounted(() => {
  loadTemplates()
  loadBlocks()
  loadNoteTemplates()
  loadVatRates()
})

const appliesToOptions = computed(() => [
  { label: t('contentLibrary.template.appliesToQUOTE'), value: 'QUOTE' },
  { label: t('contentLibrary.template.appliesToINVOICE'), value: 'INVOICE' },
  { label: t('contentLibrary.template.appliesToBOTH'), value: 'BOTH' },
])
</script>

<template>
  <div class="content-library-page">
    <n-layout>
      <AppHeader />

      <n-layout-content class="app-content">
        <div class="content-library-container">
          <h2>{{ t('contentLibrary.title') }}</h2>

          <n-tabs type="line" animated>

            <!-- Document Templates -->
            <n-tab-pane name="templates" :tab="t('contentLibrary.template.tabTitle')">
              <div class="tab-toolbar">
                <n-button type="primary" @click="openCreateTemplate">
                  <template #icon><n-icon><AddOutline /></n-icon></template>
                  {{ t('contentLibrary.template.create') }}
                </n-button>
              </div>
              <n-spin :show="templatesLoading">
                <n-data-table
                  :columns="templateColumns"
                  :data="templates"
                  :bordered="false"
                  :row-key="(row: DocumentTemplateRead) => row.id"
                  striped
                />
                <n-text v-if="!templatesLoading && templates.length === 0" depth="3" style="display: block; text-align: center; padding: 32px">
                  {{ t('contentLibrary.template.empty') }}
                </n-text>
              </n-spin>
            </n-tab-pane>

            <!-- Content Blocks -->
            <n-tab-pane name="blocks" :tab="t('contentLibrary.block.tabTitle')">
              <div class="tab-toolbar">
                <n-button type="primary" @click="openCreateBlock">
                  <template #icon><n-icon><AddOutline /></n-icon></template>
                  {{ t('contentLibrary.block.create') }}
                </n-button>
              </div>
              <n-spin :show="blocksLoading">
                <n-data-table
                  :columns="blockColumns"
                  :data="blocks"
                  :bordered="false"
                  :row-key="(row: ContentBlockRead) => row.id"
                  striped
                />
                <n-text v-if="!blocksLoading && blocks.length === 0" depth="3" style="display: block; text-align: center; padding: 32px">
                  {{ t('contentLibrary.block.empty') }}
                </n-text>
              </n-spin>
            </n-tab-pane>

            <!-- Note Templates -->
            <n-tab-pane name="notes" :tab="t('contentLibrary.note.tabTitle')">
              <div class="tab-toolbar">
                <n-button type="primary" @click="openCreateNote">
                  <template #icon><n-icon><AddOutline /></n-icon></template>
                  {{ t('contentLibrary.note.create') }}
                </n-button>
              </div>
              <n-spin :show="noteTemplatesLoading">
                <n-data-table
                  :columns="noteColumns"
                  :data="noteTemplates"
                  :bordered="false"
                  :row-key="(row: NoteTemplateRead) => row.id"
                  striped
                />
                <n-text v-if="!noteTemplatesLoading && noteTemplates.length === 0" depth="3" style="display: block; text-align: center; padding: 32px">
                  {{ t('contentLibrary.note.empty') }}
                </n-text>
              </n-spin>
            </n-tab-pane>

          </n-tabs>
        </div>
      </n-layout-content>
    </n-layout>

    <!-- Document Template Modal -->
    <n-modal
      v-model:show="showTemplateModal"
      preset="card"
      :title="editingTemplate ? t('contentLibrary.template.edit') : t('contentLibrary.template.create')"
      style="max-width: 720px"
    >
      <n-form label-placement="top">
        <n-grid :cols="2" :x-gap="16">
          <n-gi>
            <n-form-item :label="t('contentLibrary.template.name')" required>
              <n-input v-model:value="templateName" :placeholder="t('contentLibrary.template.namePlaceholder')" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item :label="t('contentLibrary.template.appliesTo')">
              <n-select v-model:value="templateAppliesTo" :options="appliesToOptions" />
            </n-form-item>
          </n-gi>
        </n-grid>

        <!-- Template lines -->
        <div style="margin-top: 8px; margin-bottom: 4px; font-weight: 600">{{ t('contentLibrary.template.lines') }}</div>
        <div v-for="(line, i) in templateLines" :key="i" style="border: 1px solid var(--n-border-color); border-radius: 4px; padding: 12px; margin-bottom: 8px">
          <n-grid :cols="12" :x-gap="8" :y-gap="4">
            <n-gi :span="6">
              <n-form-item :label="t('contentLibrary.template.lineName')" size="small" required>
                <n-input v-model:value="line.name" :placeholder="t('contentLibrary.template.lineNamePlaceholder')" />
              </n-form-item>
            </n-gi>
            <n-gi :span="2">
              <n-form-item :label="t('contentLibrary.template.lineQty')" size="small">
                <n-input-number v-model:value="line.quantity" :min="0.001" :precision="3" />
              </n-form-item>
            </n-gi>
            <n-gi :span="2">
              <n-form-item :label="t('contentLibrary.template.lineUnit')" size="small">
                <n-input v-model:value="line.unit_name" clearable @update:value="() => { line.unit_id = null }" />
              </n-form-item>
            </n-gi>
            <n-gi :span="2">
              <n-form-item :label="t('contentLibrary.template.linePrice')" size="small">
                <n-input-number v-model:value="line.unit_price" :min="0" :precision="3" />
              </n-form-item>
            </n-gi>
            <n-gi :span="12">
              <n-form-item :label="t('contentLibrary.template.lineDesc')" size="small">
                <n-input v-model:value="line.description" clearable />
              </n-form-item>
            </n-gi>
            <n-gi :span="3">
              <n-form-item :label="t('contentLibrary.template.lineVatRate')" size="small">
                <n-select
                  v-model:value="line.vat_rate_id"
                  :options="vatRateOptions"
                  clearable
                  size="small"
                />
              </n-form-item>
            </n-gi>
            <n-gi :span="3">
              <n-form-item :label="t('contentLibrary.template.lineDiscountType')" size="small">
                <n-select v-model:value="line.discount_type" :options="discountTypeOptions" size="small" />
              </n-form-item>
            </n-gi>
            <n-gi v-if="line.discount_type !== 'NONE'" :span="3">
              <n-form-item :label="t('contentLibrary.template.lineDiscountValue')" size="small">
                <n-input-number v-model:value="line.discount_value" :min="0" :precision="3" size="small" />
              </n-form-item>
            </n-gi>
            <n-gi :span="line.discount_type !== 'NONE' ? 2 : 5" />
            <n-gi :span="1" style="display: flex; align-items: flex-end; padding-bottom: 2px; justify-content: flex-end">
              <n-button
                v-if="templateLines.length > 1"
                size="small"
                quaternary
                circle
                type="error"
                @click="templateLines.splice(i, 1)"
              >
                <template #icon><n-icon><TrashOutline /></n-icon></template>
              </n-button>
            </n-gi>
          </n-grid>
        </div>
        <n-button dashed block @click="templateLines.push(emptyTemplateLine())">
          <template #icon><n-icon><AddOutline /></n-icon></template>
          {{ t('contentLibrary.template.addLine') }}
        </n-button>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showTemplateModal = false">{{ t('contentLibrary.cancel') }}</n-button>
          <n-button type="primary" :loading="templateSaving" @click="saveTemplate">{{ t('contentLibrary.save') }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- Content Block Modal -->
    <n-modal
      v-model:show="showBlockModal"
      preset="card"
      :title="editingBlock ? t('contentLibrary.block.edit') : t('contentLibrary.block.create')"
      style="max-width: 560px"
    >
      <n-form label-placement="top">
        <n-grid :cols="2" :x-gap="16">
          <n-gi>
            <n-form-item :label="t('contentLibrary.block.name')" required>
              <n-input v-model:value="blockName" :placeholder="t('contentLibrary.block.namePlaceholder')" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item :label="t('contentLibrary.block.kind')">
              <n-select v-model:value="blockKind" :options="kindOptions" />
            </n-form-item>
          </n-gi>
          <n-gi :span="2">
            <n-form-item :label="t('contentLibrary.block.body')" required>
              <n-input v-model:value="blockBody" type="textarea" :autosize="{ minRows: 3 }" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item :label="t('contentLibrary.block.isDefault')">
              <n-switch v-model:value="blockIsDefault" />
            </n-form-item>
          </n-gi>
        </n-grid>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showBlockModal = false">{{ t('contentLibrary.cancel') }}</n-button>
          <n-button type="primary" :loading="blockSaving" @click="saveBlock">{{ t('contentLibrary.save') }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- Note Template Modal -->
    <n-modal
      v-model:show="showNoteTemplateModal"
      preset="card"
      :title="editingNoteTemplate ? t('contentLibrary.note.edit') : t('contentLibrary.note.create')"
      style="max-width: 480px"
    >
      <n-form label-placement="top">
        <n-form-item :label="t('contentLibrary.note.name')" required>
          <n-input v-model:value="noteName" :placeholder="t('contentLibrary.note.namePlaceholder')" />
        </n-form-item>
        <n-form-item :label="t('contentLibrary.note.body')" required>
          <n-input v-model:value="noteBody" type="textarea" :autosize="{ minRows: 3 }" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showNoteTemplateModal = false">{{ t('contentLibrary.cancel') }}</n-button>
          <n-button type="primary" :loading="noteSaving" @click="saveNote">{{ t('contentLibrary.save') }}</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.app-content {
  min-height: calc(100vh - 57px);
}

.content-library-container {
  max-width: 1100px;
  margin: 0 auto;
  padding: 24px;
}

.content-library-container h2 {
  margin-top: 0;
}

.tab-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}
</style>
