<script setup lang="ts">
/**
 * Product create / edit page – cost, category, unit, margin, VAT defaults.
 * Also includes an inline category management modal.
 * Internal cost/margin fields: owner-only, never exposed to customer-facing outputs.
 */
import { onMounted, ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  useMessage, useDialog,
  NButton, NSpace, NInput, NForm, NFormItem, NCard, NSpin, NAlert,
  NDivider, NInputNumber, NSelect, NModal, NSwitch,
} from 'naive-ui'
import { useProductsStore } from '../../stores/products'
import { useVatStore } from '../../stores/vat'
import { useUnitStore } from '../../stores/dictionaries'
import type { components } from '../../api/schema'

type ProductCategoryWrite = components['schemas']['ProductCategoryWrite']

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()
const store = useProductsStore()
const vatStore = useVatStore()
const unitStore = useUnitStore()

const isEdit = ref(false)
const loading = ref(false)
const saving = ref(false)
const pageError = ref<string | null>(null)

// Product form fields.
const name = ref('')
const sku = ref<string | null>(null)
const categoryId = ref<string | null>(null)
const unitId = ref<string | null>(null)
const purchaseCost = ref<number | null>(null)
const marginRate = ref<number | null>(null)
const defaultVatRateId = ref<string | null>(null)
const supplier = ref<string | null>(null)
const active = ref(true)

// Category modal state.
const showCategoryModal = ref(false)
const catModalName = ref('')
const catModalMarginRate = ref<number | null>(null)
const catModalActive = ref(true)
const catSaving = ref(false)
const editingCatId = ref<string | null>(null)

onMounted(async () => {
  await Promise.all([
    store.fetchCategories(),
    vatStore.fetchRates(),
    unitStore.fetchAll(),
  ])

  const id = route.params.id as string | undefined
  if (id && id !== 'new') {
    isEdit.value = true
    loading.value = true
    try {
      const product = await store.fetchProduct(id)
      name.value = product.name
      sku.value = product.sku ?? null
      categoryId.value = product.category_id ?? null
      unitId.value = product.unit_id ?? null
      purchaseCost.value = product.purchase_cost_excl_vat != null
        ? Number(product.purchase_cost_excl_vat)
        : null
      marginRate.value = product.margin_rate != null
        ? Number(product.margin_rate) * 100
        : null
      defaultVatRateId.value = product.default_vat_rate_id ?? null
      supplier.value = product.supplier ?? null
      active.value = product.active
    } catch {
      pageError.value = t('products.loadFailed')
    } finally {
      loading.value = false
    }
  }
})

const categoryOptions = computed(() =>
  store.categories.map((c) => ({ label: c.name, value: c.id })),
)

const unitOptions = computed(() =>
  unitStore.items.map((u) => ({ label: `${u.name} (${u.code})`, value: u.id })),
)

const vatRateOptions = computed(() =>
  vatStore.rates
    .filter((r) => r.active)
    .map((r) => ({ label: r.label, value: r.id })),
)

/** Compute effective margin from the selected category's default, for display hint. */
const categoryDefaultMargin = computed(() => {
  if (!categoryId.value) return null
  const cat = store.categories.find((c) => c.id === categoryId.value)
  if (!cat || cat.default_margin_rate == null) return null
  return Number(cat.default_margin_rate) * 100
})

async function handleSave() {
  if (!name.value.trim()) {
    message.error(t('products.nameRequired'))
    return
  }
  saving.value = true
  try {
    const payload = {
      name: name.value.trim(),
      sku: sku.value?.trim() || null,
      category_id: categoryId.value || null,
      unit_id: unitId.value || null,
      purchase_cost_excl_vat:
        purchaseCost.value != null ? String(purchaseCost.value.toFixed(3)) : null,
      margin_rate:
        marginRate.value != null ? String((marginRate.value / 100).toFixed(4)) : null,
      default_vat_rate_id: defaultVatRateId.value || null,
      supplier: supplier.value?.trim() || null,
      extra: {},
      active: active.value,
    }
    if (isEdit.value) {
      await store.updateProduct(route.params.id as string, payload)
    } else {
      await store.createProduct(payload)
    }
    message.success(t('products.saveSuccess'))
    router.push('/products')
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    message.error(msg || t('products.saveFailed'))
  } finally {
    saving.value = false
  }
}

function handleCancel() {
  router.push('/products')
}

// ---------------------------------------------------------------------------
// Inline category management modal
// ---------------------------------------------------------------------------

function openCreateCategoryModal() {
  editingCatId.value = null
  catModalName.value = ''
  catModalMarginRate.value = null
  catModalActive.value = true
  showCategoryModal.value = true
}

async function handleSaveCategory() {
  if (!catModalName.value.trim()) {
    message.error(t('products.category.nameRequired'))
    return
  }
  catSaving.value = true
  try {
    const payload: ProductCategoryWrite = {
      name: catModalName.value.trim(),
      default_margin_rate:
        catModalMarginRate.value != null
          ? (String((catModalMarginRate.value / 100).toFixed(4)) as unknown as number)
          : null,
      active: catModalActive.value,
    }
    if (editingCatId.value) {
      await store.updateCategory(editingCatId.value, payload)
    } else {
      const created = await store.createCategory(payload)
      categoryId.value = created.id
    }
    message.success(t('products.category.saveSuccess'))
    showCategoryModal.value = false
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    message.error(msg || t('products.category.saveFailed'))
  } finally {
    catSaving.value = false
  }
}

function openEditCategoryModal(id: string) {
  const cat = store.categories.find((c) => c.id === id)
  if (!cat) return
  editingCatId.value = id
  catModalName.value = cat.name
  catModalMarginRate.value =
    cat.default_margin_rate != null ? Number(cat.default_margin_rate) * 100 : null
  catModalActive.value = cat.active
  showCategoryModal.value = true
}

function handleDeleteCategory(id: string, _name: string) {
  dialog.warning({
    title: t('products.category.delete'),
    content: t('products.category.deleteConfirm'),
    positiveText: t('products.category.delete'),
    negativeText: t('products.cancel'),
    onPositiveClick: async () => {
      try {
        await store.deleteCategory(id)
        if (categoryId.value === id) categoryId.value = null
        message.success(t('products.category.deleteSuccess'))
      } catch {
        message.error(t('products.category.deleteFailed'))
      }
    },
  })
}
</script>

<template>
  <div class="product-edit-page">
    <div class="product-edit-container">
          <h2>{{ isEdit ? t('products.edit') : t('products.create') }}</h2>

          <n-alert v-if="pageError" type="error" style="margin-bottom: 16px">
            {{ pageError }}
          </n-alert>

          <n-spin :show="loading">
            <n-card>
              <n-form label-placement="left" label-width="160">
                <!-- Basic info -->
                <n-divider>{{ t('products.infoSection') }}</n-divider>

                <n-form-item :label="t('products.name')" required>
                  <n-input
                    v-model:value="name"
                    :placeholder="t('products.namePlaceholder')"
                  />
                </n-form-item>

                <n-form-item :label="t('products.sku')">
                  <n-input
                    v-model:value="sku"
                    :placeholder="t('products.skuPlaceholder')"
                  />
                </n-form-item>

                <n-form-item :label="t('products.category')">
                  <n-space align="center">
                    <n-select
                      v-model:value="categoryId"
                      :options="categoryOptions"
                      :placeholder="t('products.categoryPlaceholder')"
                      clearable
                      style="width: 220px"
                    />
                    <n-button size="small" @click="openCreateCategoryModal">
                      + {{ t('products.category.add') }}
                    </n-button>
                    <n-button
                      v-if="categoryId"
                      size="small"
                      quaternary
                      @click="openEditCategoryModal(categoryId)"
                    >
                      {{ t('products.category.edit') }}
                    </n-button>
                    <n-button
                      v-if="categoryId"
                      size="small"
                      quaternary
                      type="error"
                      @click="handleDeleteCategory(categoryId, '')"
                    >
                      {{ t('products.category.delete') }}
                    </n-button>
                  </n-space>
                  <div
                    v-if="categoryDefaultMargin != null && marginRate == null"
                    class="category-margin-hint"
                  >
                    {{ t('products.categoryDefaultMarginHint', { pct: categoryDefaultMargin.toFixed(1) }) }}
                  </div>
                </n-form-item>

                <n-form-item :label="t('products.unit')">
                  <n-select
                    v-model:value="unitId"
                    :options="unitOptions"
                    :placeholder="t('products.unitPlaceholder')"
                    clearable
                    style="width: 220px"
                  />
                </n-form-item>

                <n-form-item :label="t('products.supplier')">
                  <n-input
                    v-model:value="supplier"
                    :placeholder="t('products.supplierPlaceholder')"
                  />
                </n-form-item>

                <!-- Cost section (internal) -->
                <n-divider>{{ t('products.costSection') }}</n-divider>

                <n-form-item :label="t('products.cost')">
                  <n-input-number
                    v-model:value="purchaseCost"
                    :placeholder="t('products.costPlaceholder')"
                    :precision="3"
                    :min="0"
                    style="width: 200px"
                  />
                </n-form-item>

                <n-form-item :label="t('products.marginRate')">
                  <n-input-number
                    v-model:value="marginRate"
                    :placeholder="t('products.marginRatePlaceholder')"
                    :precision="2"
                    :min="0"
                    :max="9999.99"
                    style="width: 200px"
                  >
                    <template #suffix>%</template>
                  </n-input-number>
                  <div
                    v-if="marginRate == null && categoryDefaultMargin != null"
                    class="field-hint"
                  >
                    {{ t('products.inheritsFromCategory', { pct: categoryDefaultMargin.toFixed(1) }) }}
                  </div>
                </n-form-item>

                <n-form-item :label="t('products.defaultVatRate')">
                  <n-select
                    v-model:value="defaultVatRateId"
                    :options="vatRateOptions"
                    :placeholder="t('products.defaultVatRatePlaceholder')"
                    clearable
                    style="width: 220px"
                  />
                </n-form-item>

                <!-- Status -->
                <n-divider>{{ t('products.statusSection') }}</n-divider>

                <n-form-item :label="t('dict.active')">
                  <n-switch v-model:value="active" />
                </n-form-item>
              </n-form>

              <n-space justify="end" style="margin-top: 16px">
                <n-button @click="handleCancel">{{ t('products.cancel') }}</n-button>
                <n-button type="primary" :loading="saving" @click="handleSave">
                  {{ t('products.save') }}
                </n-button>
              </n-space>
            </n-card>
          </n-spin>
    </div>

    <!-- Category management modal -->
    <n-modal v-model:show="showCategoryModal" preset="card" style="width: 400px" :title="editingCatId ? t('products.category.edit') : t('products.category.add')">
      <n-form label-placement="left" label-width="120">
        <n-form-item :label="t('products.category.name')" required>
          <n-input v-model:value="catModalName" :placeholder="t('products.category.namePlaceholder')" />
        </n-form-item>
        <n-form-item :label="t('products.category.defaultMargin')">
          <n-input-number
            v-model:value="catModalMarginRate"
            :placeholder="t('products.category.defaultMarginPlaceholder')"
            :precision="2"
            :min="0"
            :max="9999.99"
            style="width: 150px"
          >
            <template #suffix>%</template>
          </n-input-number>
        </n-form-item>
        <n-form-item :label="t('dict.active')">
          <n-switch v-model:value="catModalActive" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showCategoryModal = false">{{ t('products.cancel') }}</n-button>
          <n-button type="primary" :loading="catSaving" @click="handleSaveCategory">
            {{ t('products.save') }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.product-edit-container {
  max-width: 720px;
  margin: 0 auto;
  padding: 24px;
}

.product-edit-container h2 {
  margin: 0 0 16px;
}

.category-margin-hint,
.field-hint {
  font-size: 12px;
  color: var(--n-text-color-3, #888);
  margin-top: 4px;
}
</style>
